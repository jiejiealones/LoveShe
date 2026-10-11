"""Application operations independent of the Windows browser UI."""

import hashlib
import json
import sqlite3
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

from .core import (add_memory, build_request, import_json, memory_rows,
                   record_memory_version, statistics, update_memory, utc_now)


def require_contact(db, cid):
    row = db.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
    if not row:
        raise ValueError("联系人不存在。")
    return dict(row)


def text(value, limit=20000, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"请输入不超过 {limit} 字的文字。")
    value = value.strip()
    if required and not value:
        raise ValueError("内容不能为空。")
    return value


def contact_list(db):
    return [dict(r) for r in db.execute("""SELECT c.*,
        (SELECT COUNT(*) FROM messages m WHERE m.contact_id=c.id) AS message_count,
        (SELECT MAX(ts) FROM messages m WHERE m.contact_id=c.id) AS last_message,
        (SELECT COUNT(*) FROM memories m WHERE m.contact_id=c.id AND deleted_at IS NULL) AS memory_count
        FROM contacts c ORDER BY last_message DESC,c.display_name""")]


def save_contact(db, data):
    cid = data.get("contact") or uuid.uuid4().hex
    name = text(data.get("name", ""), 100, True)
    goal = text(data.get("goal", ""), 4000)
    background = text(data.get("background", ""), 12000)
    with db:
        db.execute("""INSERT INTO contacts(id,display_name,goal,background) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
            goal=excluded.goal,background=excluded.background,context_epoch=contacts.context_epoch+1""",
                   (cid, name, goal, background))
    return {"contact": cid}


def details(db, cid):
    from .rolling_summary import current
    contact = require_contact(db, cid)
    return {"contact": contact, "statistics": statistics(db, cid), "rolling_summary":current(db,cid), "memories": memory_rows(db, cid),
            "candidates": [dict(r) for r in db.execute("SELECT * FROM memory_candidates WHERE contact_id=? AND state='pending' ORDER BY id DESC", (cid,))],
            "consultations": [dict(r) for r in db.execute("SELECT * FROM consultations WHERE contact_id=? AND mode!='memory' ORDER BY id DESC LIMIT 100", (cid,))],
            "feedback": [dict(r) for r in db.execute("SELECT * FROM feedback WHERE contact_id=? ORDER BY id DESC LIMIT 100", (cid,))],
            "binding": dict(r) if (r := db.execute("SELECT adapter,last_sync FROM source_bindings WHERE contact_id=?", (cid,)).fetchone()) else None,
            "imports": [dict(r) for r in db.execute("SELECT * FROM ingest_events WHERE contact_id=? ORDER BY id DESC LIMIT 10", (cid,))]}


def import_payload(db, root, cid, payload, source="file", start=None, end=None):
    contact = require_contact(db, cid)
    # Shared ingress boundary. Future listener/OCR adapters also use this path.
    with tempfile.TemporaryDirectory(prefix="ingest-", dir=root) as tmp:
        path = Path(tmp) / "input.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = import_json(db, path, cid, contact["display_name"], start, end)
    with db:
        db.execute("INSERT INTO ingest_events(contact_id,source,result,created_at) VALUES(?,?,?,?)",
                   (cid, source, json.dumps(result), utc_now()))
    return result


def context_stamp(db, cid):
    contact = require_contact(db, cid)
    # No transmission if data changed after the user's preview.
    parts = [contact, dict(db.execute("SELECT COUNT(*) n,MAX(rowid) last FROM messages WHERE contact_id=?", (cid,)).fetchone()),
             dict(db.execute("SELECT COUNT(*) n,MAX(id) last FROM feedback WHERE contact_id=?", (cid,)).fetchone())]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def preview(db, data):
    cid = data["contact"]
    task = data.get("task", "consult")
    question = text(data.get("question", ""), 20000, task != 'advise')
    if task == 'advise':
        question = ('请根据此联系人的长期记忆和最近对话，针对当前最需要回应的消息，直接给我三个意图不同的自然回复草稿。'
                    '先简短说明正在回应哪条消息及时间；若最后一句是我发的，不要假装对方已回复，可以建议等待或自然跟进。'
                    '每个方案分别列出：意图、可直接复制的回复、简短原因及后续建议。证据不足时说明，不编造背景。'
                    + ('\n本次补充要求：'+question if question else ''))
    if task not in {"consult", "analyze", "advise", "extract"}:
        raise ValueError("无效的咨询类型。")
    parent = data.get("parent_id") or None
    if task == 'advise':
        parent=None
        data={**data,'history':True,'memory':True,'start':None,'end':None}
    if data.get("start") and data.get("end") and data["start"] > data["end"]:
        raise ValueError("开始日期不能晚于结束日期。")
    mode = "consult" if task == "extract" else task
    body, mids, memids = build_request(db, cid, mode, question,
                                     bool(data.get("history", True)), bool(data.get("memory", True)),
                                     parent, data.get("start") or None, data.get("end") or None)
    if task == "extract":
        body[0]["content"] += (
            '只输出 JSON 对象 {"candidates":[{"category":"event|preference|background",'
            '"content":"待用户审核的简短事实候选","evidence_ids":["完整消息ID"]}]}。'
            '最多5条，不能把心理推测当作事实。消息证据ID须来自给定资料；'
            '用户主动提供的背景可无消息证据。不能声称已保存记忆。')
    return {"contact": cid, "mode": mode, "task": task, "question": question,
            "payload": body, "message_ids": mids, "memory_ids": memids,
            "parent_id": parent, "context_epoch": require_contact(db, cid)["context_epoch"],
            "stamp": context_stamp(db, cid)}


def collect_candidates(db, prepared, result):
    answer = result["answer"].strip()
    if answer.startswith("```"):
        answer = answer.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        items = json.loads(answer)["candidates"]
        if not isinstance(items, list):
            raise ValueError()
        validated = []
        for item in items[:5]:
            content = text(item["content"], 2000, True)
            ids = item.get("evidence_ids", [])
            if not isinstance(ids, list) or any(i not in prepared["message_ids"] for i in ids):
                raise ValueError("模型候选引用了未发送的消息，未保存候选。可查看原始回答后重试。")
            validated.append((content, text(item.get("category", "event"), 50), ids))
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"candidate_count": 0, "warning": "模型没有返回有效候选格式；原始回答已保存，可重试或手动添加。"}
    with db:
        for content, category, ids in validated:
            db.execute("INSERT INTO memory_candidates(contact_id,content,category,evidence_ids,consultation_id,created_at) VALUES(?,?,?,?,?,?)",
                       (prepared["contact"], content, category, json.dumps(ids), result["consultation_id"], utc_now()))
    return {"candidate_count": len(validated)}


def mutate_memory(db, data):
    cid = data["contact"]
    require_contact(db, cid)
    action = data["action"]
    if action == "add":
        return {"id": add_memory(db, cid, text(data.get("category", "background"), 50, True),
                                 text(data.get("content", ""), 12000, True), data.get("status", "unconfirmed"),
                                 data.get("evidence_ids", []))}
    if action in {"edit", "delete"}:
        content = text(data.get("content", ""), 12000, True) if action == "edit" else None
        category = text(data["category"], 50, True) if action == "edit" and "category" in data else None
        if not update_memory(db, cid, int(data["id"]), content, data.get("status"), action == "delete", category):
            raise ValueError("记忆不存在或已删除。")
        return {"updated": True}
    if action == "restore":
        row = db.execute("SELECT * FROM memory_versions WHERE id=? AND contact_id=?", (int(data["version"]), cid)).fetchone()
        if not row:
            raise ValueError("版本不存在。")
        old = json.loads(row["snapshot"])
        if old.get("deleted_at"):
            raise ValueError("请选择删除前的版本。")
        with db:
            record_memory_version(db, cid, row["memory_id"], "before_restore")
            db.execute("UPDATE memories SET content=?,status=?,category=?,evidence_ids=?,deleted_at=NULL,updated_at=? WHERE id=? AND contact_id=?",
                       (old["content"], old["status"], old["category"], old["evidence_ids"], utc_now(), row["memory_id"], cid))
            record_memory_version(db, cid, row["memory_id"], "restore")
            db.execute("UPDATE contacts SET context_epoch=context_epoch+1 WHERE id=?", (cid,))
        return {"restored": True}
    raise ValueError("无效的记忆操作。")


def resolve_candidate(db, data):
    row = db.execute("SELECT * FROM memory_candidates WHERE id=? AND contact_id=? AND state='pending'",
                     (int(data["id"]), data["contact"])).fetchone()
    if not row:
        raise ValueError("候选不存在或已处理。")
    if data["action"] == "accept":
        mid = add_memory(db, data["contact"], text(data.get("category", row["category"]), 50, True),
                         text(data.get("content", row["content"]), 12000, True),
                         data.get("status", "fact"), json.loads(row["evidence_ids"]))
        state = "accepted"
    elif data["action"] == "reject":
        mid, state = None, "rejected"
    else:
        raise ValueError("无效的候选操作。")
    with db:
        db.execute("UPDATE memory_candidates SET state=? WHERE id=?", (state, row["id"]))
    return {"memory_id": mid}


def export_contact(db, cid):
    result = {"format": "loveshe-backup-v1", "exported_at": utc_now(), "contact": require_contact(db, cid)}
    for table in ("messages", "memories", "memory_versions", "memory_candidates", "consultations", "feedback", "transmissions", "source_bindings", "ingest_events", "memory_policies", "automation", "memory_windows", "memory_state", "memory_runs", "memory_processed_messages", "contact_summaries"):
        result[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE contact_id=?", (cid,))]
    return result


def delete_contact(db, cid, confirmation):
    contact = require_contact(db, cid)
    if confirmation != contact["display_name"]:
        raise ValueError("请输入完整联系人备注确认删除。")
    with db:
        for table in ("contact_summaries", "automation", "memory_windows", "memory_state", "memory_runs", "memory_processed_messages", "memory_versions", "memory_candidates", "source_bindings", "ingest_events", "memory_policies", "transmissions", "feedback", "consultations", "memories", "messages"):
            db.execute(f"DELETE FROM {table} WHERE contact_id=?", (cid,))
        db.execute("DELETE FROM contacts WHERE id=?", (cid,))
    return {"deleted": True}


def backup_database(root, name="before-windows-v1.sqlite3"):
    path = root / "pilot.sqlite3"
    if not path.exists():
        return
    target = root / "backups"
    target.mkdir(parents=True, exist_ok=True)
    # One backup per application version before migrations or normal use.
    backup = target / name
    if not backup.exists():
        with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(backup)) as dest:
            src.backup(dest)
