"""App-owned contact polling and background memory maintenance.

Runs only while LoveShe is running. This is not a Codex scheduled automation.
"""
import json
import threading
import time
from datetime import datetime, timedelta
from . import memory_engine as memory, service
from .adapters import WeFlow
from .core import LOCAL_TZ, call_model, open_db, utc_now


class Automation:
    def __init__(self,app):
        self.app=app
        self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self.run,name='loveshe-automation',daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def configure(self,db,cid,data):
        service.require_contact(db,cid)
        capture=data.get('capture_enabled') is True
        automatic=data.get('auto_memory') is True
        days=int(data.get('recent_days',14));limit=int(data.get('recent_limit',120))
        interval=int(data.get('poll_seconds',15));batch=0
        if not 1<=days<=90 or not 20<=limit<=300 or not 10<=interval<=300:
            raise ValueError('请输入有效范围：近期1–90天、20–300条；监听10–300秒。')
        if capture and not db.execute('SELECT 1 FROM source_bindings WHERE contact_id=?',(cid,)).fetchone():
            raise ValueError('请先绑定微信会话。')
        info=self.app.model_info()
        old=memory.policy(db,cid)
        if automatic and (not old['auto_memory'] or old['consent_model']!=info['model'] or old['consent_endpoint']!=info['endpoint']):
            if data.get('consent') is not True:
                raise ValueError('请确认自动维护会分批向当前模型发送该联系人的聊天、记忆、咨询与反馈。')
        with db:
            db.execute('INSERT INTO automation(contact_id) VALUES(?) ON CONFLICT DO NOTHING',(cid,))
            db.execute('UPDATE automation SET capture_enabled=?,auto_memory=?,poll_seconds=?,memory_seconds=?,recent_days=?,recent_limit=?,consent_model=?,consent_endpoint=?,consent_at=?,next_poll=0,next_memory=0,failures=0,last_error=? WHERE contact_id=?',
                (capture,automatic,interval,batch,days,limit,info['model'],info['endpoint'],utc_now() if automatic else None,'',cid))
            db.execute("UPDATE automation SET capture_error='',memory_error='',capture_failures=0,memory_failures=0 WHERE contact_id=?",(cid,))
            db.execute('INSERT INTO memory_policies(contact_id,auto_write,auto_overwrite) VALUES(?,?,?) ON CONFLICT(contact_id) DO UPDATE SET auto_write=excluded.auto_write,auto_overwrite=excluded.auto_overwrite',
                (cid,automatic,data.get('auto_overwrite') is True))
        memory.write_files(db,self.app.root,cid)
        return memory.policy(db,cid)

    def run(self):
        while not self.stop_event.wait(1):
            db=None
            try:
                db=open_db(self.app.root)
                for row in db.execute('SELECT contact_id FROM automation WHERE capture_enabled=1 OR auto_memory=1').fetchall():
                    if self.stop_event.is_set():
                        break
                    self.tick(db,row[0])
            except Exception:
                # Per-contact failures are recorded by their jobs; a transient DB
                # conflict must not kill the coordinator.
                pass
            finally:
                if db is not None:
                    db.close()

    def tick(self,db,cid):
        p=memory.policy(db,cid);now=time.time()
        with self.app.lock:
            if cid in self.app.busy or len(self.app.busy)>=3:
                return
            if p['capture_enabled'] and p['next_poll']<=now and f'capture:{cid}' not in self.app.busy:
                with db:
                    db.execute('UPDATE automation SET next_poll=? WHERE contact_id=?',(now+p['poll_seconds'],cid))
                self.app.job(cid,'监听并同步微信增量',lambda conn:self.capture(conn,cid),slot=f'capture:{cid}')
                return
            if not p['auto_memory'] or p['next_memory']>now or f'memory:{cid}' in self.app.busy or f'capture:{cid}' in self.app.busy:
                return
            info=self.app.model_info()
            if (p['consent_model'],p['consent_endpoint'])!=(info['model'],info['endpoint']):
                with db:
                    db.execute("UPDATE automation SET auto_memory=0,memory_error='模型配置已改变，自动外发已暂停，请重新确认。' WHERE contact_id=?",(cid,))
                return
            state=db.execute('SELECT * FROM memory_state WHERE contact_id=?',(cid,)).fetchone()
            today=datetime.now(LOCAL_TZ).date().isoformat()
            unfinished=db.execute('SELECT 1 FROM memory_windows WHERE contact_id=?',(cid,)).fetchone()
            summary=memory.rolling_summary.current(db,cid)
            # Daily checkpoints, never a fixed 300-second memory timer. Same-day
            # changes wait for tomorrow or an explicit Update-to-today request.
            if state and state['checkpoint_day']==today and not unfinished and summary['sections']:
                return
            self.app.job(cid,'自动压缩并更新联系人记忆',lambda conn:self.compress(conn,cid),slot=f'memory:{cid}')

    def failure(self,db,cid,error,kind):
        p=memory.policy(db,cid);failures=p[kind+'_failures']+1
        wait=min(3600, max(30, 60 if kind=='memory' else p['poll_seconds'])*2**min(failures,5))
        with db:
            column='next_memory' if kind=='memory' else 'next_poll'
            db.execute(f'UPDATE automation SET {kind}_failures=?,{kind}_error=?,{column}=? WHERE contact_id=?',
                (failures,str(error)[:600],time.time()+wait,cid))

    def capture(self,db,cid):
        p=memory.policy(db,cid)
        if not p['capture_enabled']:
            return {'skipped':True}
        binding=db.execute('SELECT external_id FROM source_bindings WHERE contact_id=?',(cid,)).fetchone()
        if not binding:
            raise ValueError('微信绑定不存在，监听已跳过。')
        external=binding[0]
        # Fill the gap since the last imported message on first enable. Subsequent
        # polls overlap by 120 seconds and deduplicate, including after restart.
        midnight=int(datetime.now(LOCAL_TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        latest=db.execute('SELECT MAX(ts) FROM messages WHERE contact_id=?',(cid,)).fetchone()[0]
        cursor=p['capture_cursor'] or (int(datetime.fromisoformat(latest).timestamp()) if latest else midnight)
        start=max(0,cursor-120)
        upper=int(time.time())
        flow=WeFlow(self.app.root)
        try:
            try:
                payload=flow.incremental(external,start)
            except OverflowError:
                begin=datetime.fromtimestamp(start,LOCAL_TZ).date().isoformat()
                end=datetime.fromtimestamp(upper,LOCAL_TZ).date().isoformat()
                path=flow.export(external,begin,end)
                payload=json.loads(path.read_text(encoding='utf-8-sig'))
                if payload.get('coverage',{}).get('mayHaveMore'):
                    raise ValueError('微信补齐导出仍提示不完整，未推进监听游标。')
            with self.app.lock:
                current=db.execute('SELECT external_id FROM source_bindings WHERE contact_id=?',(cid,)).fetchone()
                if not current or current[0]!=external or not memory.policy(db,cid)['capture_enabled']:
                    return {'skipped':True}
                result=service.import_payload(db,self.app.root,cid,payload,'listener')
                if result.get('invalid') or result.get('unknown_sender') or result.get('source_may_have_more'):
                    raise ValueError('增量数据存在无法解析或方向未知的内容，已导入有效消息但未推进游标，请核对。')
                with db:
                    db.execute('UPDATE automation SET capture_cursor=?,last_poll=?,capture_error=?,capture_failures=0 WHERE contact_id=?',(upper,utc_now(),'',cid))
                    db.execute('UPDATE source_bindings SET last_sync=? WHERE contact_id=?',(utc_now(),cid))
            return result
        except Exception as exc:
            self.failure(db,cid,exc,'capture');raise

    def compress(self,db,cid):
        """Refresh the entire date gap and drain every bounded batch before committing the day."""
        p=memory.policy(db,cid);info=self.app.model_info()
        if not p['auto_memory'] or (p['consent_model'],p['consent_endpoint'])!=(info['model'],info['endpoint']):
            return {'skipped':True}
        try:
            w=db.execute('SELECT * FROM memory_windows WHERE contact_id=?',(cid,)).fetchone()
            if not w:
                start,end=memory.date_range(db,cid)
                coverage=memory.progress(db,cid)
                summary_only=coverage['checkpoint_day']==end and not coverage['pending'] and not memory.rolling_summary.current(db,cid)['sections']
                binding=db.execute('SELECT external_id FROM source_bindings WHERE contact_id=?',(cid,)).fetchone()
                if binding and not summary_only:
                    path=WeFlow(self.app.root).export(binding[0],start,end)
                    payload=json.loads(path.read_text(encoding='utf-8-sig'))
                    if payload.get('coverage',{}).get('mayHaveMore'):
                        raise ValueError('日期区间导出不完整，未更新记忆检查点。')
                    result=service.import_payload(db,self.app.root,cid,payload,'memory-date-window',start,end)
                    if result.get('invalid') or result.get('unknown_sender') or result.get('source_may_have_more'):
                        raise ValueError('日期区间有无法解析的消息，未更新记忆检查点。')
                with self.app.lock:
                    memory.begin_window(db,cid,start,end,summary_only=summary_only)
            updates=0;batches=0
            while not self.stop_event.is_set():
                if not memory.policy(db,cid)['auto_memory']:
                    return {'skipped':True}
                w=dict(db.execute('SELECT * FROM memory_windows WHERE contact_id=?',(cid,)).fetchone())
                result=self.compress_batch(db,cid,w)
                if result.get('skipped'): return result
                if result.get('empty'):
                    with self.app.lock: memory.finish_window(db,self.app.root,cid)
                    return {'memory_updates':updates,'batches':batches,'date_start':w['start_day'],'date_end':w['end_day'],'coverage':memory.progress(db,cid)}
                updates+=result['memory_updates'];batches+=1
            return {'skipped':True}
        except Exception as exc:
            self.failure(db,cid,exc,'memory');raise

    def compress_batch(self,db,cid,window=None):
        prepared=None
        try:
            with self.app.lock:
                p=memory.policy(db,cid);info=self.app.model_info()
                if not p['auto_memory'] or (p['consent_model'],p['consent_endpoint'])!=(info['model'],info['endpoint']):
                    return {'skipped':True}
                prepared=memory.prepare(db,cid,window)
            if not prepared:
                return {'memory_updates':0,'empty':True,'coverage':memory.progress(db,cid)}
            # Exact request is committed to memory_runs before the external call;
            # persistent per-contact consent authorizes this bounded scope.
            result=call_model(db,cid,'memory',prepared['question'],preview_only=False,prepared=prepared)
            with self.app.lock:
                p=memory.policy(db,cid)
                if not p['auto_memory']:
                    with db:
                        db.execute("UPDATE memory_runs SET status='cancelled',error='自动维护已暂停，返回结果未应用。' WHERE id=?",(prepared['run_id'],))
                    return {'skipped':True}
                overwrite=db.execute('SELECT auto_overwrite FROM memory_policies WHERE contact_id=?',(cid,)).fetchone()
                output=memory.apply_result(db,self.app.root,prepared,result['answer'],bool(overwrite and overwrite[0]))
                with db:
                    db.execute('UPDATE automation SET last_memory=?,memory_error=?,memory_failures=0 WHERE contact_id=?',(utc_now(),'',cid))
            return output
        except Exception as exc:
            if prepared:
                with db:
                    db.execute("UPDATE memory_runs SET status='error',error=? WHERE id=?",(str(exc)[:1000],prepared['run_id']))
            raise
