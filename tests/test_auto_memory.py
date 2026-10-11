import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from loveshe import memory_engine as memory, service
from loveshe.core import open_db, import_json, build_request, update_memory, add_memory, utc_now
from loveshe.server import serve


class AutoMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        env=patch.dict('os.environ',{'LOVESHE_DATA_DIR':self.tmp.name,'LOVESHE_BACKEND':'api',
                                   'LOVESHE_MODEL':'','LOVESHE_API_BASE':'','LOVESHE_API_KEY':''})
        env.start()
        self.addCleanup(env.stop)
        self.app=serve(self.root,0)
        self.db=open_db(self.root)
        fixture=Path(__file__).parent/'fixtures'/'weflow_a.json'
        import_json(self.db,fixture,'a','A')
        import_json(self.db,fixture,'b','B')

    def tearDown(self):
        self.app.app.automation.stop()
        self.app.server_close()
        self.app.app.pool.shutdown(wait=True)
        self.db.close()
        self.tmp.cleanup()

    def blank_summary(self):
        return {k:{'text':'','status':'unconfirmed','evidence_ids':[],'memory_ids':[]} for k in memory.rolling_summary.SECTIONS}

    def output(self,p,content='周六两点看展，五点半结束。',key='weekend_plan'):
        return json.dumps({'entries':[{'key':key,'category':'event','content':content,
            'status':'fact','evidence_ids':p['message_ids'][:1],
            'consultation_ids':[],'feedback_ids':[],'supersede_ids':[]}],'rolling_summary':self.blank_summary()})

    def enable(self,cid='a',**kwargs):
        data=dict(auto_memory=True,consent=True,auto_overwrite=True)
        data.update(kwargs)
        return self.app.app.automation.configure(self.db,cid,data)

    def test_incremental_resume_files_and_contact_scope(self):
        p=memory.prepare(self.db,'a')
        with self.assertRaises(ValueError):
            memory.apply_result(self.db,self.root,p,json.dumps({'entries':[{'key':'bad','content':'x','category':'event','status':'fact','evidence_ids':[self.db.execute("SELECT id FROM messages WHERE contact_id='b' LIMIT 1").fetchone()[0]]}]}))
        self.assertEqual(memory.progress(self.db,'a')['processed'],0)
        result=memory.apply_result(self.db,self.root,p,self.output(p))
        self.assertEqual(result['memory_updates'],1)
        self.assertEqual(memory.progress(self.db,'a')['pending'],0)
        self.assertIsNone(memory.prepare(self.db,'a'))
        self.assertEqual(memory.progress(self.db,'b')['processed'],0)
        folder=memory.contact_dir(self.root,'a')
        self.assertIn('周六两点', (folder/'MEMORY.md').read_text(encoding='utf-8'))
        self.assertTrue((folder/'memory_summary.md').exists())
        self.assertEqual(len(list((folder/'updates').glob('*.md'))),1)
        self.assertNotEqual(folder,memory.contact_dir(self.root,'../../a'))

    def test_manual_correction_and_deletion_cannot_be_overwritten(self):
        p=memory.prepare(self.db,'a');memory.apply_result(self.db,self.root,p,self.output(p))
        mid=self.db.execute("SELECT id FROM memories WHERE contact_id='a'").fetchone()[0]
        update_memory(self.db,'a',mid,'用户确认改成周日。','fact')
        # New message creates a new incremental batch about the same topic.
        service.import_payload(self.db,self.root,'a',[{'timestamp':1750001200,'sender':'me','content':'周末安排'}])
        p=memory.prepare(self.db,'a')
        memory.apply_result(self.db,self.root,p,self.output(p,'模型尝试覆盖'))
        self.assertEqual(self.db.execute('SELECT content FROM memories WHERE id=?',(mid,)).fetchone()[0],'用户确认改成周日。')
        update_memory(self.db,'a',mid,delete=True)
        service.import_payload(self.db,self.root,'a',[{'timestamp':1750001500,'sender':'them','content':'再谈安排'}])
        p=memory.prepare(self.db,'a');memory.apply_result(self.db,self.root,p,self.output(p))
        self.assertIsNotNone(self.db.execute('SELECT deleted_at FROM memories WHERE id=?',(mid,)).fetchone()[0])
        body,_,ids=build_request(self.db,'a','consult','怎么安排？')
        self.assertNotIn(mid,ids)
        self.assertNotIn('用户确认改成周日',body[1]['content'])

    def test_stale_results_rejected_and_recent_context_automatic(self):
        p=memory.prepare(self.db,'a')
        service.save_contact(self.db,{'contact':'a','name':'A','background':'新的纠正'})
        with self.assertRaises(ValueError):
            memory.apply_result(self.db,self.root,p,self.output(p))
        self.assertEqual(memory.progress(self.db,'a')['processed'],0)
        service.import_payload(self.db,self.root,'a',[{'timestamp':'2026-10-10T12:00:00+08:00','sender':'me','content':'最近消息'}])
        body,mids,_=build_request(self.db,'a','consult','近期如何？')
        self.assertEqual(len(mids),1)
        self.assertIn('最近消息',body[1]['content'])
        self.assertNotIn('这周六下午要不要',body[1]['content'])

    def test_inferred_details_cannot_be_promoted_to_facts(self):
        p=memory.prepare(self.db,'a')
        memory.apply_result(self.db,self.root,p,self.output(p,'推定下周六见面，实际日期待确认。'))
        self.assertEqual(self.db.execute("SELECT status FROM memories WHERE contact_id='a'").fetchone()[0],'unconfirmed')

    def test_message_aliases_resolve_only_within_prepared_batch(self):
        p=memory.prepare(self.db,'a')
        item=json.loads(self.output(p))['entries'][0]
        item['evidence_ids']=['m999999']
        with self.assertRaises(ValueError):
            memory.apply_result(self.db,self.root,p,json.dumps({'entries':[item],'rolling_summary':self.blank_summary()}))
        self.assertEqual(memory.progress(self.db,'a')['processed'],0)
        sequence=next(k for k in p['message_aliases'] if k.isdigit())
        item['evidence_ids']=['m1',int(sequence)]
        memory.apply_result(self.db,self.root,p,json.dumps({'entries':[item],'rolling_summary':self.blank_summary()}))
        evidence=json.loads(self.db.execute("SELECT evidence_ids FROM memories WHERE contact_id='a'").fetchone()[0])
        self.assertEqual(evidence,[p['message_ids'][0]])
        self.assertNotIn('sequence',json.loads(p['payload'][1]['content'].split('资料(JSON):\n')[-1])['messages'][0])

    def test_memory_update_button_reuses_running_job(self):
        app=self.app.app
        app.busy.add('memory:a')
        app.jobs['existing']={'id':'existing','contact':'a','slot':'memory:a','state':'running'}
        result=app.post('/api/memory-now',{'contact':'a'},self.db)
        self.assertEqual(result,{'job':'existing','already_running':True})
        self.assertEqual(len(app.jobs),1)
        app.busy.clear()

    def test_recall_bounds_large_memory_and_accumulated_citations(self):
        mid=self.db.execute("SELECT id FROM messages WHERE contact_id='a' LIMIT 1").fetchone()[0]
        add_memory(self.db,'a','event','非常长的记忆。'*2000,'fact',[mid]*500)
        recalled=memory.recall_memories(self.db,'a','重要背景')
        self.assertLessEqual(len(recalled[0]['evidence_ids']),8)
        self.assertLessEqual(len(json.dumps(recalled,ensure_ascii=False)),12000)
        self.assertTrue(recalled[0]['truncated'])

    def test_automatic_model_scope_consent_pause_and_failure(self):
        with self.assertRaises(ValueError):
            self.enable(consent=False)
        self.enable()
        def fake(db,cid,mode,question,**kwargs):
            return {'answer':self.output(kwargs['prepared'])}
        with patch('loveshe.automation.call_model',side_effect=fake) as model:
            result=self.app.app.automation.compress(self.db,'a')
            self.assertEqual(result['memory_updates'],1)
            model.assert_called_once()
            self.app.app.automation.compress(self.db,'a')
            model.assert_called_once()  # No recursive memory consultations or duplicates.
        service.import_payload(self.db,self.root,'a',[{'timestamp':utc_now(),'sender':'them','content':'新消息'}])
        before=memory.progress(self.db,'a')['processed']
        with patch('loveshe.automation.call_model',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.app.app.automation.compress(self.db,'a')
        self.assertEqual(memory.progress(self.db,'a')['processed'],before)
        self.assertGreater(memory.policy(self.db,'a')['next_memory'],0)
        self.assertEqual(self.db.execute('SELECT status FROM memory_runs ORDER BY id DESC LIMIT 1').fetchone()[0],'error')
        self.enable(auto_memory=False)
        with patch('loveshe.automation.call_model') as model:
            self.app.app.automation.compress(self.db,'a');model.assert_not_called()

    def test_date_window_full_range_resume_and_inclusive_checkpoint(self):
        # The previous complete update was Oct 5. Boundary days must both enter.
        with self.db:
            self.db.execute("DELETE FROM messages WHERE contact_id='a'")
            self.db.execute("INSERT INTO memory_state(contact_id,checkpoint_day) VALUES('a','2026-10-05')")
        rows=[{'timestamp':f'2026-10-{day:02d}T00:00:00+08:00','sender':'them','content':f'日程 {day} {i}'} for day in range(4,13) for i in range(30)]
        service.import_payload(self.db,self.root,'a',rows)
        self.enable()
        count=0
        seen=[]
        def fake(db,cid,mode,question,**kwargs):
            nonlocal count
            count+=1
            if count==2: raise RuntimeError('network interrupted')
            seen.extend(kwargs['prepared']['message_ids'])
            return {'answer':json.dumps({'entries':[],'rolling_summary':self.blank_summary()})}
        with patch('loveshe.automation.memory.date_range',return_value=('2026-10-05','2026-10-11')), patch('loveshe.automation.call_model',side_effect=fake):
            with self.assertRaises(RuntimeError): self.app.app.automation.compress(self.db,'a')
        self.assertEqual(memory.progress(self.db,'a')['checkpoint_day'],'2026-10-05')
        self.assertEqual(memory.progress(self.db,'a')['window']['position'],160)
        self.assertEqual(memory.progress(self.db,'a')['processed'],160)
        def resume(db,cid,mode,question,**kwargs):
            seen.extend(kwargs['prepared']['message_ids'])
            return {'answer':json.dumps({'entries':[],'rolling_summary':self.blank_summary()})}
        with patch('loveshe.automation.call_model',side_effect=resume):
            result=self.app.app.automation.compress(self.db,'a')
        self.assertEqual(len(seen),210)
        self.assertEqual(len(set(seen)),210)
        self.assertEqual(result['coverage']['checkpoint_day'],'2026-10-11')
        self.assertIsNone(result['coverage']['window'])
        dates={r[0][:10] for r in self.db.execute("SELECT ts FROM messages WHERE id IN ("+','.join('?' for _ in seen)+")",seen)}
        self.assertNotIn('2026-10-12',dates)
        # Next run overlaps the previous calendar day to include late arrivals.
        self.assertEqual(memory.date_range(self.db,'a','2026-10-12'),('2026-10-11','2026-10-12'))

    def test_date_update_fetches_bound_wechat_before_freezing_window(self):
        self.enable()
        with self.db:
            self.db.execute("INSERT INTO source_bindings(contact_id,adapter,external_id) VALUES('a','weflow','wxid_a')")
        export=self.root/'export.json'
        export.write_text('{"messages":[{"timestamp":"2026-10-11T10:00:00+08:00","sender":"them","content":"新增消息"}]}',encoding='utf-8')
        with patch('loveshe.automation.WeFlow.export',return_value=export) as fetch, patch('loveshe.automation.call_model',return_value={'answer':json.dumps({'entries':[],'rolling_summary':self.blank_summary()})}) as model:
            result=self.app.app.automation.compress(self.db,'a')
        fetch.assert_called_once()
        self.assertIn('新增消息',model.call_args.kwargs['prepared']['payload'][1]['content'])
        self.assertIsNotNone(result['coverage']['checkpoint_day'])

    def test_direct_reply_needs_no_question_and_always_uses_contact_context(self):
        add_memory(self.db,'a','preference','A 喜欢简短回复','fact',[])
        add_memory(self.db,'b','preference','B 的秘密偏好','fact',[])
        prepared=service.preview(self.db,{'contact':'a','task':'advise','history':False,'memory':False})
        self.assertTrue(prepared['message_ids'])
        self.assertTrue(prepared['memory_ids'])
        self.assertIn('三个意图不同',prepared['question'])
        self.assertIn('A 喜欢简短回复',prepared['payload'][1]['content'])
        self.assertNotIn('B 的秘密偏好',prepared['payload'][1]['content'])
        self.assertIsNone(prepared['parent_id'])

    def test_rolling_portrait_relationship_sources_and_correction(self):
        p=memory.prepare(self.db,'a')
        answer=json.loads(self.output(p))
        answer['rolling_summary']['portrait']={'text':'喜欢看展。','status':'fact','evidence_ids':['m1']}
        answer['rolling_summary']['relationship']={'text':'双方有见面邀约；关系阶段仍待确认。','status':'fact','evidence_ids':['m1']}
        memory.apply_result(self.db,self.root,p,json.dumps(answer))
        summary=memory.rolling_summary.current(self.db,'a')
        self.assertIn('看展',summary['sections']['portrait']['text'])
        self.assertEqual(summary['sections']['relationship']['status'],'unconfirmed')
        self.assertFalse(memory.rolling_summary.current(self.db,'b')['sections'])
        body,_,_=build_request(self.db,'a','advise','如何回复？')
        self.assertIn('喜欢看展',body[1]['content'])
        service.import_payload(self.db,self.root,'a',[{'timestamp':utc_now(),'sender':'them','content':'后来约好了'}])
        p2=memory.prepare(self.db,'a')
        self.assertIn('喜欢看展',p2['payload'][1]['content'])
        bad=json.loads(self.output(p2));bad['rolling_summary']['portrait']={'text':'假的画像','status':'fact','evidence_ids':['not-in-contact']}
        with self.assertRaises(ValueError): memory.apply_result(self.db,self.root,p2,json.dumps(bad))
        update_memory(self.db,'a',self.db.execute("SELECT id FROM memories WHERE contact_id='a' LIMIT 1").fetchone()[0],'纠正：不喜欢看展。')
        self.assertTrue(memory.rolling_summary.current(self.db,'a')['stale'])
        body,_,_=build_request(self.db,'a','consult','再问')
        self.assertNotIn('喜欢看展。',body[1]['content'].split('rolling_summary')[1].split('messages')[0])

    def test_daily_trigger_has_no_same_day_interval_and_resumes_next_day(self):
        self.enable()
        p=memory.prepare(self.db,'a');memory.apply_result(self.db,self.root,p,self.output(p))
        from loveshe.core import LOCAL_TZ
        from datetime import datetime
        today=datetime.now(LOCAL_TZ).date().isoformat()
        with self.db:
            self.db.execute('UPDATE memory_state SET checkpoint_day=? WHERE contact_id=?',(today,'a'))
        service.import_payload(self.db,self.root,'a',[{'timestamp':utc_now(),'sender':'them','content':'当天新消息'}])
        with patch.object(self.app.app,'job') as job:
            self.app.app.automation.tick(self.db,'a');job.assert_not_called()
            with self.db: self.db.execute("UPDATE memory_state SET checkpoint_day='2026-01-01' WHERE contact_id='a'")
            self.app.app.automation.tick(self.db,'a');job.assert_called_once()
        self.assertEqual(memory.policy(self.db,'a')['memory_update_mode'],'daily')
        self.assertNotIn('memory_seconds',memory.policy(self.db,'a'))

    def test_summary_upgrade_uses_completed_local_memory_without_wechat_export(self):
        self.enable()
        p=memory.prepare(self.db,'a');memory.apply_result(self.db,self.root,p,self.output(p))
        from loveshe.core import LOCAL_TZ
        from datetime import datetime
        today=datetime.now(LOCAL_TZ).date().isoformat()
        with self.db:
            self.db.execute("DELETE FROM contact_summaries WHERE contact_id='a'")
            self.db.execute("UPDATE memory_state SET checkpoint_day=? WHERE contact_id='a'",(today,))
            self.db.execute("INSERT INTO source_bindings(contact_id,adapter,external_id) VALUES('a','weflow','wxid_a')")
        answer={'entries':[],'rolling_summary':self.blank_summary()}
        with patch('loveshe.automation.WeFlow.export',side_effect=RuntimeError('offline')) as export,patch('loveshe.automation.call_model',return_value={'answer':json.dumps(answer)}) as model:
            result=self.app.app.automation.compress(self.db,'a')
        export.assert_not_called();model.assert_called_once()
        self.assertEqual(result['coverage']['checkpoint_day'],today)
        self.assertTrue(memory.rolling_summary.current(self.db,'a')['sections'])

    def test_live_capture_deduplicates_and_keeps_cursor_on_invalid_input(self):
        with self.db:
            self.db.execute("INSERT INTO source_bindings(contact_id,adapter,external_id) VALUES('a','weflow','wxid_a')")
        self.enable(capture_enabled=True,auto_memory=False)
        sample={'source':'weflow-live','messages':[{'createTime':1750000000,'isSend':1,'localId':1,'localType':1,'content':'这周六下午要不要去看展？'}]}
        with patch('loveshe.automation.WeFlow.incremental',return_value=sample):
            result=self.app.app.automation.capture(self.db,'a')
        self.assertEqual(result.get('imported',0),0)
        self.assertEqual(result['duplicates'],1)
        cursor=memory.policy(self.db,'a')['capture_cursor']
        with patch('loveshe.automation.WeFlow.incremental',return_value={'messages':[{'content':'no timestamp'}]}):
            with self.assertRaises(ValueError):
                self.app.app.automation.capture(self.db,'a')
        self.assertEqual(memory.policy(self.db,'a')['capture_cursor'],cursor)
        service.delete_contact(self.db,'a','A');memory.remove_files(self.root,'a')
        self.assertFalse(memory.contact_dir(self.root,'a').exists())
        for table in ['automation','memory_state','memory_runs']:
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE contact_id=?',('a',)).fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
