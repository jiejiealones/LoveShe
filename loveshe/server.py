"""Loopback-only Windows app. Run python -m loveshe.server."""

import argparse
import json
import os
import secrets
import socket
import sqlite3
import tempfile
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import service
from . import memory_engine as memory
from .automation import Automation
from .adapters import CAPABILITIES, WeFlow
from .core import LOCAL_TZ, call_model, data_path, local_midnight_utc, open_db, utc_now


STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        service.backup_database(self.root, "before-date-memory-v3.sqlite3")
        with closing(open_db(self.root)) as db:
            # Interrupted generations are retried from the last committed cursor.
            with db:
                db.execute("UPDATE memory_runs SET status='interrupted',error='服务重启，进度未推进；下次重试。' WHERE status='prepared'")
            for row in db.execute('SELECT id FROM contacts').fetchall():
                memory.write_files(db,self.root,row[0])
        self.token = secrets.token_hex(32)
        self.previews = {}
        self.jobs = {}
        self.lock = threading.RLock()
        self.busy = set()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="loveshe")
        self.automation = Automation(self)

    def model_info(self):
        from .config import public_model_info
        return public_model_info()

    def assert_idle(self, cid):
        if cid in self.busy:
            raise ValueError("该联系人还有任务运行，请完成后再修改数据。")

    def job(self, cid, name, function, slot=None):
        key = slot or cid
        with self.lock:
            self.assert_idle(key)
            if len([j for j in self.jobs.values() if j["state"] in ("queued", "running")]) >= 3:
                raise ValueError("已有三个任务运行，请稍后再试。")
            jid = secrets.token_hex(12)
            self.busy.add(key)
            self.jobs[jid] = {"id": jid, "contact": cid, "slot":key, "name": name, "state": "queued", "created_at": utc_now()}

        def run():
            with self.lock:
                self.jobs[jid]["state"] = "running"
            db = open_db(self.root)
            try:
                result = function(db)
                with self.lock:
                    self.jobs[jid].update(state="done", result=result)
            except Exception as exc:
                with self.lock:
                    self.jobs[jid].update(state="error", error=str(exc)[:1000])
            finally:
                db.close()
                with self.lock:
                    self.busy.discard(key)
                    # Keep recent completed tasks for page refresh/recovery.
                    finished = [k for k, v in self.jobs.items() if v["state"] in ("done", "error")]
                    for finished_key in finished[:-50]:
                        del self.jobs[finished_key]
        self.pool.submit(run)
        return {"job": jid}

    def get(self, route, q, db):
        cid = q.get("contact", "")
        if route == "/api/state":
            with self.lock:
                jobs = [dict(v) for v in self.jobs.values()]
            return {"contacts": service.contact_list(db), "model": self.model_info(),
                    "capabilities": CAPABILITIES, "jobs": jobs,
                    "data_path": str(self.root), "version": "LoveShe 0.1.0 · Windows 实验版"}
        if route == "/api/contact":
            result = service.details(db, cid)
            result.update(automation=memory.policy(db,cid), memory_coverage=memory.progress(db,cid),
                          memory_directory=str(memory.contact_dir(self.root,cid)))
            row=db.execute('SELECT auto_overwrite FROM memory_policies WHERE contact_id=?',(cid,)).fetchone()
            result['auto_overwrite']=bool(row[0]) if row else True
            return result
        if route == "/api/memory-runs":
            service.require_contact(db,cid)
            return [dict(r) for r in db.execute('SELECT * FROM memory_runs WHERE contact_id=? ORDER BY id DESC LIMIT 30',(cid,))]
        if route == "/api/messages":
            service.require_contact(db, cid)
            sql = " FROM messages WHERE contact_id=?"
            args = [cid]
            if q.get("prefix"):
                prefix = q["prefix"].lower()
                if len(prefix) < 8 or any(c not in "0123456789abcdef" for c in prefix):
                    raise ValueError("请输入至少8位消息 ID。")
                sql += " AND id LIKE ?"
                args.append(prefix + "%")
            if q.get("query"):
                sql += " AND instr(content,?)>0"
                args.append(q["query"])
            if q.get("start"):
                sql += " AND ts>=?"
                args.append(local_midnight_utc(q["start"]))
            if q.get("end"):
                from datetime import date, timedelta
                sql += " AND ts<?"
                args.append(local_midnight_utc((date.fromisoformat(q["end"]) + timedelta(days=1)).isoformat()))
            total = db.execute("SELECT COUNT(*)" + sql, args).fetchone()[0]
            offset = max(0, int(q.get("offset", 0)))
            rows = db.execute("SELECT id,ts,sender,content,kind" + sql + " ORDER BY ts DESC,id DESC LIMIT 100 OFFSET ?", [*args, offset]).fetchall()
            return {"messages": [dict(r) for r in reversed(rows)], "total": total, "offset": offset}
        if route == "/api/versions":
            service.require_contact(db, cid)
            return [dict(r) for r in db.execute("SELECT * FROM memory_versions WHERE contact_id=? ORDER BY id DESC LIMIT 200", (cid,))]
        if route == "/api/job":
            with self.lock:
                job = self.jobs.get(q.get("id"))
                if not job:
                    raise ValueError("任务已过期。可在咨询记录中查看结果。")
                return dict(job)
        raise ValueError("接口不存在。")

    def post(self, route, data, db):
        cid = data.get("contact", "")
        with self.lock:
            if route != "/api/automation":
                self.assert_idle(cid)
            if route == "/api/automation":
                return self.automation.configure(db,cid,data)
            if route == "/api/memory-now":
                service.require_contact(db,cid)
                active=next((j for j in self.jobs.values() if j.get('slot')==f'memory:{cid}' and j['state'] in ('queued','running')),None)
                if active:
                    return {'job':active['id'],'already_running':True}
                if not memory.policy(db,cid)['auto_memory']:
                    raise ValueError('请先启用自动记忆并确认发送范围。')
                return self.job(cid,'补齐日期区间并更新全部记忆',lambda conn:self.automation.compress(conn,cid),slot=f'memory:{cid}')
            if route == "/api/contact":
                result=service.save_contact(db, data)
                memory.write_files(db,self.root,result['contact'])
                return result
            if route == "/api/import":
                return service.import_payload(db, self.root, cid, data["payload"], start=data.get("start") or None, end=data.get("end") or None)
            if route == "/api/memory":
                result=service.mutate_memory(db, data)
                memory.write_files(db,self.root,cid)
                return result
            if route == "/api/candidate":
                result=service.resolve_candidate(db, data)
                memory.write_files(db,self.root,cid)
                return result
            if route == "/api/feedback":
                service.require_contact(db, cid)
                consultation = int(data["consultation_id"])
                if not db.execute("SELECT 1 FROM consultations WHERE id=? AND contact_id=?", (consultation, cid)).fetchone():
                    raise ValueError("咨询记录不属于此联系人。")
                outcome = data.get("outcome", "observed")
                if outcome not in {"used", "edited", "rejected", "observed"}:
                    raise ValueError("无效的反馈类型。")
                note = service.text(data.get("note", ""), 12000, True)
                with db:
                    fid = db.execute("INSERT INTO feedback(contact_id,consultation_id,outcome,note,created_at) VALUES(?,?,?,?,?)",
                                     (cid, consultation, outcome, note, utc_now())).lastrowid
                return {"id": fid}
            if route == "/api/preview":
                prepared = service.preview(db, data)
                prepared["model_info"] = self.model_info()
                pid = secrets.token_hex(16)
                now = time.time()
                self.previews = {k: v for k, v in self.previews.items() if now - v["time"] < 900}
                if len(self.previews) > 30:
                    raise ValueError("预览过多，请关闭部分页面或15分钟后重试。")
                self.previews[pid] = {"prepared": prepared, "time": now}
                return {"preview_id": pid, **prepared}
            if route == "/api/send":
                cached = self.previews.get(data.get("preview_id"))
                if not cached or time.time() - cached["time"] > 900:
                    raise ValueError("预览已过期，请重新预览后确认。")
                prepared = cached["prepared"]
                cid = prepared["contact"]
                self.assert_idle(cid)
                if prepared["stamp"] != service.context_stamp(db, cid) or prepared["model_info"] != self.model_info():
                    raise ValueError("资料已更新，请重新预览发送内容。")
                if data.get("confirmed") is not True:
                    raise ValueError("请确认发送范围。")
                def inference(connection):
                    result = call_model(connection, cid, prepared["mode"], prepared["question"], preview_only=False, prepared=prepared)
                    if prepared["task"] == "extract":
                        result.update(service.collect_candidates(connection, prepared, result))
                    return result
                result = self.job(cid, "生成记忆候选" if prepared["task"] == "extract" else "AI 正在分析", inference)
                del self.previews[data["preview_id"]]
                return result
            if route == "/api/sessions":
                return self.job("__sessions__", "读取微信联系人", lambda connection: {"sessions": WeFlow(self.root).sessions()})
            if route == "/api/bind":
                if f'capture:{cid}' in self.busy:
                    raise ValueError('正在读取微信增量，请稍后再更换绑定。')
                service.require_contact(db, cid)
                external_id = service.text(data["external_id"], 200, True)
                with db:
                    db.execute("INSERT INTO source_bindings(contact_id,adapter,external_id) VALUES(?,?,?) ON CONFLICT(contact_id) DO UPDATE SET external_id=excluded.external_id,last_sync=NULL", (cid, "weflow", external_id))
                    db.execute('UPDATE automation SET capture_cursor=0 WHERE contact_id=?',(cid,))
                return {"bound": True}
            if route == "/api/sync":
                contact = service.require_contact(db, cid)
                binding = db.execute("SELECT external_id FROM source_bindings WHERE contact_id=?", (cid,)).fetchone()
                if not binding:
                    raise ValueError("请先在数据管理中选择并绑定微信联系人。")
                external_id = binding[0]
                start, end = data.get("start"), data.get("end")
                if not start or not end:
                    raise ValueError("请选择更新起止日期。")
                def sync(connection):
                    path = WeFlow(self.root).export(external_id, start, end)
                    payload = json.loads(path.read_text(encoding="utf-8-sig"))
                    result = service.import_payload(connection, self.root, cid, payload, "weflow", start, end)
                    with connection:
                        connection.execute("UPDATE source_bindings SET last_sync=? WHERE contact_id=?", (utc_now(), cid))
                    return result
                return self.job(cid, "导出并更新微信记录", sync)
            if route == "/api/delete":
                if f'memory:{cid}' in self.busy or f'capture:{cid}' in self.busy:
                    raise ValueError('请先暂停自动跟进，等待当前任务结束后删除。')
                result = service.delete_contact(db, cid, data.get("confirmation"))
                memory.remove_files(self.root,cid)
                self.previews = {k: v for k, v in self.previews.items() if v["prepared"]["contact"] != cid}
                for key in [k for k, v in self.jobs.items() if v["contact"] == cid]:
                    del self.jobs[key]
                return result
        raise ValueError("接口不存在。")


class Handler(BaseHTTPRequestHandler):
    server_version = "LoveShe/1"

    def log_message(self, *args):
        pass  # URLs and private identifiers are not logged.

    @property
    def app(self):
        return self.server.app

    def respond(self, value, code=200, mime="application/json; charset=utf-8", filename=None):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def authorized(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in hosts}:
            return False
        return secrets.compare_digest(self.headers.get("X-LoveShe-Token", ""), self.app.token)

    def dispatch(self, method):
        parsed = urlsplit(self.path)
        route = parsed.path
        try:
            if not route.startswith("/api/") and method == "GET":
                names = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/style.css": ("style.css", "text/css; charset=utf-8")}
                if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                    return self.respond({"error": "禁止访问"}, 403)
                if route not in names:
                    return self.respond({"error": "不存在"}, 404)
                name, mime = names[route]
                content = (STATIC / name).read_bytes()
                if name == "index.html":
                    content = content.replace(b"__TOKEN__", self.app.token.encode())
                return self.respond(content, mime=mime)
            if not self.authorized():
                return self.respond({"error": "此页面无权访问本机数据，请从启动入口重新打开。"}, 403)
            q = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            db = open_db(self.app.root)
            try:
                if method == "GET" and route == "/api/export":
                    return self.respond(json.dumps(service.export_contact(db, q["contact"]), ensure_ascii=False, indent=2).encode(), filename="loveshe-contact.json")
                if method == "GET" and route == "/api/backup":
                    with tempfile.TemporaryDirectory(dir=self.app.root) as tmp:
                        path = Path(tmp) / "backup.sqlite3"
                        with closing(sqlite3.connect(path)) as dest:
                            db.backup(dest)
                        return self.respond(path.read_bytes(), mime="application/octet-stream", filename="loveshe-backup.sqlite3")
                if method == "GET":
                    result = self.app.get(route, q, db)
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 32 * 1024 * 1024:
                        raise ValueError("上传内容为空或超过32MB。")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("请求格式错误。")
                    result = self.app.post(route, data, db)
                return self.respond(result)
            finally:
                db.close()
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Exception:
            self.respond({"error": "本地操作失败。请检查数据格式或稍后重试。"}, 500)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR otherwise permits a second process on the same port.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(root=None, port=8765):
    server = LocalServer(("127.0.0.1", port), Handler)
    try:
        server.app = App(root or data_path())
    except Exception:
        server.server_close()
        raise
    return server


def main():
    parser = argparse.ArgumentParser(description="LoveShe Windows 本机应用")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        server = serve(port=args.port)
    except OSError as exc:
        # Double-clicking the launcher again should open the running app.
        from urllib.request import urlopen
        url = f"http://127.0.0.1:{args.port}"
        try:
            with urlopen(url, timeout=2) as response:
                existing = response.headers.get("Server", "").startswith("LoveShe/1")
        except Exception:
            existing = False
        if existing:
            print(f"LoveShe 已在运行：{url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
        print(f"无法启动本机服务：{exc}。可使用 --port 指定其他端口。", flush=True)
        return
    url = f"http://127.0.0.1:{server.server_port}"
    server.app.automation.start()
    print(f"LoveShe 已启动：{url}（关闭此终端可停止）", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.automation.stop()
        server.server_close()
        server.app.pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
