"""Versioned, evidence-backed portrait and relationship context for one contact."""
import json

SECTIONS={'portrait':'人物画像','relationship':'关系现状与变化','communication':'互动与沟通模式','open_questions':'尚待确认与后续关注'}


def init_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS contact_summaries (id INTEGER PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id), payload TEXT NOT NULL, context_epoch INTEGER NOT NULL, updated_at TEXT NOT NULL, run_id INTEGER REFERENCES memory_runs(id))')
    db.commit()


def current(db,cid):
    row=db.execute('SELECT * FROM contact_summaries WHERE contact_id=? ORDER BY id DESC LIMIT 1',(cid,)).fetchone()
    epoch=db.execute('SELECT context_epoch FROM contacts WHERE id=?',(cid,)).fetchone()[0]
    if not row:
        return {'sections':{},'stale':False,'updated_at':None}
    if row['context_epoch']!=epoch:
        return {'sections':{},'stale':True,'updated_at':row['updated_at']}
    return {'id':row['id'],'sections':json.loads(row['payload']),'stale':False,'updated_at':row['updated_at']}


def validate(db,prepared,raw,aliases):
    if raw is None:
        if prepared.get('requires_summary'):
            raise ValueError('缺少人物画像与关系滚动摘要，未推进进度。')
        return None  # Compatibility for previously returned legacy responses.
    if not isinstance(raw,dict) or set(raw)!=set(SECTIONS):
        raise ValueError('滚动摘要必须包含人物画像、关系现状、互动模式和待确认事项。')
    validated={}
    for key in SECTIONS:
        claim=raw[key]
        if not isinstance(claim,dict) or not isinstance(claim.get('text'),str) or len(claim['text'])>1500 or claim.get('status') not in {'fact','inference','unconfirmed'}:
            raise ValueError('滚动摘要格式或长度无效。')
        claim=dict(claim)
        found=False
        for field,allowed in [('evidence_ids',prepared['allowed_message_ids']),('memory_ids',prepared['memory_ids']),('consultation_ids',prepared['allowed_consultations']),('feedback_ids',prepared['allowed_feedback'])]:
            ids=claim.get(field,[])
            if not isinstance(ids,list) or len(ids)>16:
                raise ValueError('滚动摘要来源格式无效。')
            if field=='evidence_ids': ids=[aliases.get(str(i),i) if isinstance(i,(str,int)) else i for i in ids]
            else:
                if field=='memory_ids':
                    ids=[i[len(prepared['contact'])+1:] if isinstance(i,str) and i.startswith(prepared['contact']+':') else i for i in ids]
                ids=[int(i) if isinstance(i,str) and i.isdigit() else i for i in ids]
            if any(i not in allowed for i in ids):
                raise ValueError('滚动摘要引用了当前联系人资料以外的来源。')
            claim[field]=list(dict.fromkeys(ids));found=found or bool(ids)
        if claim['text'].strip() and not found:
            raise ValueError('滚动摘要缺少依据；无依据的部分应留空。')
        if claim['status']=='fact':
            uncertain=any(w in claim['text'] for w in ('可能','推测','未知','未确认','不确定','待确认','似乎'))
            for mid in claim['memory_ids']:
                m=db.execute('SELECT status FROM memories WHERE id=? AND contact_id=? AND deleted_at IS NULL',(mid,prepared['contact'])).fetchone()
                uncertain=uncertain or not m or m[0]!='fact'
            if uncertain or (not claim['evidence_ids'] and not claim['memory_ids'] and not claim['feedback_ids']):
                claim['status']='unconfirmed'
        validated[key]=claim
    return validated


def save(db,cid,sections,run_id):
    from .core import utc_now
    epoch=db.execute('SELECT context_epoch FROM contacts WHERE id=?',(cid,)).fetchone()[0]
    db.execute('INSERT INTO contact_summaries(contact_id,payload,context_epoch,updated_at,run_id) VALUES(?,?,?,?,?)',
        (cid,json.dumps(sections,ensure_ascii=False),epoch,utc_now(),run_id))


def markdown(summary):
    parts=['# 人物画像与关系滚动摘要','上一版摘要结合新增聊天滚动更新；不代表对方真实心理。']
    if summary['stale']: parts.append('档案或记忆已纠正，旧摘要已停止使用；下次更新重建。')
    for key,label in SECTIONS.items():
        claim=summary['sections'].get(key,{})
        parts.extend([f'## {label}',f"[{claim.get('status','unconfirmed')}] {claim.get('text') or '尚无足够资料'}",
            '消息依据：'+', '.join(claim.get('evidence_ids',[]))])
    return '\n\n'.join(parts)+'\n'
