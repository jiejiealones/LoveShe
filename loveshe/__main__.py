"""Command line entry point: python -m loveshe --help."""

import argparse
import json
import sys
from pathlib import Path
from . import service

from .core import (add_memory, call_model, import_json, memory_rows, messages,
                   open_db, statistics, update_memory, utc_now)


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="LoveShe local pilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Import WeFlow, she-love-me or simple JSON")
    imp.add_argument("path", type=Path)
    imp.add_argument("--contact", required=True, help="Stable contact ID or local alias")
    imp.add_argument("--name", required=True)
    imp.add_argument("--start", help="Inclusive YYYY-MM-DD")
    imp.add_argument("--end", help="Inclusive YYYY-MM-DD")
    imp.add_argument("--own-id", help="Own wxid if sender direction is absent")
    sub.add_parser("contacts")
    goal = sub.add_parser("set-goal")
    goal.add_argument("contact")
    goal.add_argument("goal")
    delete = sub.add_parser("delete-contact", help="Permanently delete one contact and all pilot data")
    delete.add_argument("contact")
    export = sub.add_parser("export-contact", help="Export one contact as JSON")
    export.add_argument("contact")
    export.add_argument("path", type=Path)
    stat = sub.add_parser("stats")
    stat.add_argument("contact")
    stat.add_argument("--start")
    stat.add_argument("--end")
    hist = sub.add_parser("history")
    hist.add_argument("contact")
    hist.add_argument("--limit", type=int, default=30)
    hist.add_argument("--start")
    hist.add_argument("--end")
    hist.add_argument("--query", help="Literal substring search")
    evidence = sub.add_parser("evidence", help="Resolve a model-cited message ID prefix within one contact")
    evidence.add_argument("contact")
    evidence.add_argument("id_prefix", help="At least 8 hexadecimal characters")
    consultations = sub.add_parser("consultations")
    consultations.add_argument("contact")
    consultations.add_argument("--limit", type=int, default=20)
    mem = sub.add_parser("memory")
    mem.add_argument("action", choices=["list", "add", "edit", "delete"])
    mem.add_argument("contact")
    mem.add_argument("--id", type=int)
    mem.add_argument("--category", default="background")
    mem.add_argument("--content")
    mem.add_argument("--status", choices=["fact", "inference", "unconfirmed"], default=None)
    mem.add_argument("--evidence", action="append", default=[])
    ask = sub.add_parser("ask", help="Preview payload and optionally call external model")
    ask.add_argument("mode", choices=["analyze", "advise", "consult"])
    ask.add_argument("contact")
    ask.add_argument("question")
    ask.add_argument("--no-history", action="store_true")
    ask.add_argument("--no-memory", action="store_true")
    ask.add_argument("--send", action="store_true", help="Show payload then require SEND confirmation")
    fb = sub.add_parser("feedback")
    fb.add_argument("contact")
    fb.add_argument("outcome", choices=["used", "edited", "rejected", "observed"])
    fb.add_argument("note")
    fb.add_argument("--consultation", type=int)
    args = p.parse_args()
    db = open_db()
    try:
        if args.cmd == "import":
            dump(import_json(db, args.path, args.contact, args.name, args.start, args.end, args.own_id))
        elif args.cmd == "contacts":
            dump([dict(r) for r in db.execute("SELECT id,display_name,goal FROM contacts ORDER BY id")])
        elif args.cmd == "set-goal":
            with db:
                updated = db.execute("UPDATE contacts SET goal=? WHERE id=?", (args.goal, args.contact)).rowcount
            dump({"updated": bool(updated)})
        elif args.cmd == "export-contact":
            contact = db.execute("SELECT * FROM contacts WHERE id=?", (args.contact,)).fetchone()
            if not contact:
                raise ValueError("Unknown contact")
            result = service.export_contact(db, args.contact)
            args.path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            dump({"exported": str(args.path), "messages": len(result["messages"])})
        elif args.cmd == "delete-contact":
            if input(f"永久删除联系人 {args.contact} 的全部试验数据？输入 DELETE：").strip() != "DELETE":
                print("Cancelled")
            else:
                contact = service.require_contact(db, args.contact)
                dump(service.delete_contact(db, args.contact, contact["display_name"]))
                from .memory_engine import remove_files
                remove_files(data_path(), args.contact)
        elif args.cmd == "stats":
            dump(statistics(db, args.contact, args.start, args.end))
        elif args.cmd == "history":
            dump(messages(db, args.contact, args.limit, args.start, args.end, args.query))
        elif args.cmd == "evidence":
            if len(args.id_prefix) < 8 or any(c not in "0123456789abcdef" for c in args.id_prefix.lower()):
                raise ValueError("Message ID prefix must be at least 8 hexadecimal characters")
            rows = db.execute("SELECT id,ts,sender,content,kind FROM messages WHERE contact_id=? AND id LIKE ? ORDER BY ts,id LIMIT 10",
                              (args.contact, args.id_prefix.lower() + "%")).fetchall()
            dump([dict(r) for r in rows])
        elif args.cmd == "consultations":
            dump([dict(r) for r in db.execute("SELECT id,mode,question,answer,created_at FROM consultations WHERE contact_id=? ORDER BY id DESC LIMIT ?", (args.contact, args.limit))])
        elif args.cmd == "memory":
            if args.action == "list":
                dump(memory_rows(db, args.contact))
            elif args.action == "add":
                if not args.content:
                    p.error("memory add requires --content")
                dump({"id": add_memory(db, args.contact, args.category, args.content,
                                       args.status or "unconfirmed", args.evidence)})
            else:
                if args.id is None:
                    p.error("memory edit/delete requires --id")
                if args.action == "edit" and not args.content and not args.status:
                    p.error("memory edit requires --content or --status")
                dump({"updated": update_memory(db, args.contact, args.id, args.content,
                                                args.status, args.action == "delete")})
        elif args.cmd == "ask":
            kwargs = {"with_history": not args.no_history, "with_memory": not args.no_memory}
            preview = call_model(db, args.contact, args.mode, args.question, **kwargs)
            dump(preview)
            if args.send:
                if input("确认将以上内容发送到所示模型？输入 SEND：").strip() != "SEND":
                    print("Cancelled")
                else:
                    dump(call_model(db, args.contact, args.mode, args.question,
                                    **kwargs, preview_only=False, prepared=preview))
        elif args.cmd == "feedback":
            if args.consultation is not None:
                row = db.execute("SELECT 1 FROM consultations WHERE id=? AND contact_id=?", (args.consultation, args.contact)).fetchone()
                if not row:
                    raise ValueError("Consultation does not belong to this contact")
            with db:
                rowid = db.execute("INSERT INTO feedback(contact_id,consultation_id,outcome,note,created_at) VALUES(?,?,?,?,?)",
                                   (args.contact, args.consultation, args.outcome, args.note, utc_now())).lastrowid
            dump({"feedback_id": rowid})
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
