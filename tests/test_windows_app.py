import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loveshe import service
from loveshe.core import add_memory, build_request, import_json, open_db, update_memory, utc_now
from loveshe.server import serve


class WindowsAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        env = patch.dict("os.environ", {"LOVESHE_DATA_DIR": self.tmp.name, "LOVESHE_BACKEND": "api",
                                       "LOVESHE_MODEL": "", "LOVESHE_API_BASE": "", "LOVESHE_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)
        self.server = serve(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.db = open_db(self.root)
        fixture = Path(__file__).parent / "fixtures" / "weflow_a.json"
        import_json(self.db, fixture, "a", "A")
        import_json(self.db, fixture, "b", "B")

    def tearDown(self):
        self.db.close()
        self.server.shutdown()
        self.server.server_close()
        self.server.app.pool.shutdown(wait=True)
        self.tmp.cleanup()

    def request(self, path, data=None, token=True, origin=None):
        headers = {"X-LoveShe-Token": self.server.app.token} if token else {}
        if origin:
            headers["Origin"] = origin
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.url + "/api/" + path,
                      data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def test_local_auth_and_no_implicit_model_send(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("state", token=False)
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError):
            self.request("state", origin="https://untrusted.example")
        with patch("loveshe.server.call_model") as model:
            state = self.request("state")
            self.assertEqual(len(state["contacts"]), 2)
            p = self.request("preview", {"contact": "a", "question": "怎么回复？", "task": "advise"})
            model.assert_not_called()
            self.assertEqual(len(p["message_ids"]), 4)
            with self.assertRaises(HTTPError):
                self.request("send", {"preview_id": p["preview_id"], "confirmed": False})
            model.assert_not_called()

    def test_stale_preview_single_use_and_async_completion(self):
        data = {"contact": "a", "question": "近期如何？", "task": "analyze"}
        p = self.request("preview", data)
        self.request("memory", {"contact": "a", "action": "add", "content": "已改变的背景", "status": "unconfirmed"})
        with self.assertRaises(HTTPError) as caught:
            self.request("send", {"preview_id": p["preview_id"], "confirmed": True})
        self.assertEqual(caught.exception.code, 400)
        def fake_model(db, cid, mode, question, **kwargs):
            epoch = db.execute("SELECT context_epoch FROM contacts WHERE id=?", (cid,)).fetchone()[0]
            with db:
                cid2 = db.execute("INSERT INTO consultations(contact_id,mode,question,answer,created_at,context_epoch) VALUES(?,?,?,?,?,?)",
                                  (cid, mode, question, "模拟模型结果", utc_now(), epoch)).lastrowid
            return {"consultation_id": cid2, "answer": "模拟模型结果"}
        p = self.request("preview", data)
        with patch("loveshe.server.call_model", side_effect=fake_model) as model:
            job = self.request("send", {"preview_id": p["preview_id"], "confirmed": True})["job"]
            for _ in range(100):
                result = self.request("job?id=" + job)
                if result["state"] in {"done", "error"}:
                    break
                time.sleep(.01)
            self.assertEqual(result["state"], "done", result)
            self.assertEqual(model.call_count, 1)
            self.assertNotIn("a",self.server.app.busy)
            second=self.request("send", {"preview_id":self.request("preview",data)["preview_id"],"confirmed":True})["job"]
            for _ in range(100):
                follow=self.request("job?id="+second)
                if follow["state"] in {"done","error"}: break
                time.sleep(.01)
            self.assertEqual(follow["state"],"done",follow)
            self.assertNotIn("a",self.server.app.busy)
            with self.assertRaises(HTTPError):
                self.request("send", {"preview_id": p["preview_id"], "confirmed": True})

    def test_memory_version_isolation_restore_and_prior_context_reset(self):
        mid = add_memory(self.db, "a", "event", "旧记忆", "unconfirmed")
        epoch = service.require_contact(self.db, "a")["context_epoch"]
        with self.db:
            prior = self.db.execute("INSERT INTO consultations(contact_id,mode,question,answer,created_at,context_epoch) VALUES(?,?,?,?,?,?)",
                                    ("a", "consult", "问", "沿用旧记忆", utc_now(), epoch)).lastrowid
        before, _, _ = build_request(self.db, "a", "consult", "追问", False, True, prior)
        self.assertIn("沿用旧记忆", before[1]["content"])
        update_memory(self.db, "a", mid, "正确记忆")
        after, _, _ = build_request(self.db, "a", "consult", "追问", False, True, prior)
        self.assertNotIn("旧记忆", after[1]["content"])
        self.assertIn("正确记忆", after[1]["content"])
        with self.assertRaises(ValueError):
            build_request(self.db, "b", "consult", "追问", False, True, prior)
        versions = self.request("versions?contact=a")
        self.assertGreaterEqual(len(versions), 3)
        with self.assertRaises(HTTPError):
            self.request("memory", {"contact": "b", "action": "restore", "version": versions[0]["id"]})
        update_memory(self.db, "a", mid, delete=True)
        live = next(v for v in versions if not json.loads(v["snapshot"])["deleted_at"])
        self.request("memory", {"contact": "a", "action": "restore", "version": live["id"]})
        self.assertEqual(self.request("contact?contact=a")["memories"][0]["content"], "正确记忆")

    def test_candidate_evidence_validation_and_delete_all_tables(self):
        prepared = service.preview(self.db, {"contact": "a", "task": "extract", "question": "提取候选"})
        foreign = self.db.execute("SELECT id FROM messages WHERE contact_id='b' LIMIT 1").fetchone()[0]
        with self.assertRaises(ValueError):
            service.collect_candidates(self.db, prepared, {"consultation_id": 99, "answer": json.dumps({"candidates": [{"content": "错误证据", "evidence_ids": [foreign]}]})})
        result = service.collect_candidates(self.db, prepared, {"consultation_id": 99, "answer": json.dumps({"candidates": [{"content": "有证据的候选", "evidence_ids": [prepared["message_ids"][0]]}]})})
        self.assertEqual(result["candidate_count"], 1)
        candidate = self.db.execute("SELECT id FROM memory_candidates").fetchone()[0]
        service.resolve_candidate(self.db, {"contact": "a", "id": candidate, "action": "accept"})
        self.request("delete", {"contact": "a", "confirmation": "A"})
        self.assertEqual(len(self.request("state")["contacts"]), 1)
        for table in ("memory_candidates", "memory_versions", "memories", "messages"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE contact_id='a'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
