import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loveshe.core import (add_memory, build_request, call_model, import_json, memory_rows,
                          messages, open_db, statistics, update_memory)


class PilotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict("os.environ", {"LOVESHE_DATA_DIR": tmp.name, "LOVESHE_BACKEND": "api",
                                       "LOVESHE_MODEL": "", "LOVESHE_API_BASE": "", "LOVESHE_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_china_local_date_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = open_db(root)
            source = root / "boundary.json"
            source.write_text(json.dumps({"messages": [
                {"localId": 1, "createTime": "2026-06-30T16:30:00+00:00", "isSend": 1, "content": "July local"},
                {"localId": 2, "createTime": "2026-07-31T16:30:00+00:00", "isSend": 0, "content": "August local"},
            ]}), encoding="utf-8")
            result = import_json(db, source, "a", "A", "2026-07-01", "2026-07-31")
            self.assertEqual((result["imported"], result["out_of_range"]), (1, 1))
            self.assertEqual(statistics(db, "a")["by_month"], {"2026-07": 1})
            self.assertEqual(len(messages(db, "a", start="2026-07-01", end="2026-07-01")), 1)
            db.close()

    def test_import_dedupe_range_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = open_db(root)
            source = root / "input.json"
            source.write_text(json.dumps({"source": "weflow-cli", "messages": [
                {"localId": 1, "createTime": 1750000000, "isSend": 1, "content": "周六见"},
                {"localId": 2, "createTime": 1750000300, "isSend": 0, "content": "好"},
                {"localId": 3, "createTime": 1600000000, "isSend": 0, "content": "old"},
            ]}), encoding="utf-8")
            first = import_json(db, source, "a", "A", "2025-06-01", "2025-06-30")
            second = import_json(db, source, "a", "A", "2025-06-01", "2025-06-30")
            self.assertEqual(first["imported"], 2)
            self.assertEqual(first["out_of_range"], 1)
            self.assertEqual(second["duplicates"], 2)
            self.assertEqual(statistics(db, "a")["senders"], {"me": 1, "them": 1})
            import_json(db, source, "b", "B", "2025-06-01", "2025-06-30")
            aid = add_memory(db, "a", "event", "约定周六见", "fact")
            add_memory(db, "b", "event", "喜欢猫", "unconfirmed")
            a_request, _, _ = build_request(db, "a", "consult", "最近如何？")
            self.assertNotIn("喜欢猫", a_request[1]["content"])
            self.assertTrue(update_memory(db, "a", aid, "约定周日见", "unconfirmed"))
            self.assertEqual(memory_rows(db, "a")[0]["content"], "约定周日见")
            corrected, _, _ = build_request(db, "a", "consult", "约在何时？")
            self.assertIn("约定周日见", corrected[1]["content"])
            self.assertTrue(update_memory(db, "a", aid, delete=True))
            self.assertEqual(memory_rows(db, "a"), [])
            deleted, _, _ = build_request(db, "a", "consult", "约在何时？", False, True)
            self.assertNotIn("约定周日见", deleted[1]["content"])
            db.close()

    def test_model_preview_send_audit_and_context_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = open_db(root)
            fixture = Path(__file__).parent / "fixtures" / "weflow_a.json"
            import_json(db, fixture, "a", "A")
            add_memory(db, "a", "preference", "喜欢简短回复", "fact")
            with db:
                db.execute("INSERT INTO feedback(contact_id,outcome,note,created_at) VALUES(?,?,?,?)",
                           ("a", "observed", "真实反馈线索", "2026-01-01T00:00:00+00:00"))
            preview = call_model(db, "a", "advise", "怎么回复？")
            self.assertEqual(len(preview["message_ids"]), 4)
            self.assertEqual(len(preview["memory_ids"]), 1)
            no_context, mids, memids = build_request(db, "a", "advise", "怎么回复？", False, False)
            self.assertEqual((mids, memids), ([], []))
            self.assertNotIn("喜欢简短回复", no_context[1]["content"])
            self.assertNotIn("真实反馈线索", no_context[1]["content"])
            mock_response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "建议草稿"}}]}).encode())
            with patch.dict("os.environ", {"LOVESHE_API_BASE": "https://example.invalid/v1",
                                        # pragma: allowlist nextline secret -- fictional offline fixture
                                        "LOVESHE_MODEL": "test", "LOVESHE_API_KEY": "secret"}), \
                 patch("urllib.request.urlopen", return_value=mock_response) as post:
                sent = call_model(db, "a", "advise", "怎么回复？", preview_only=False)
            self.assertEqual(sent["answer"], "建议草稿")
            self.assertEqual(post.call_count, 1)
            audit = dict(db.execute("SELECT contact_id,model,result,message_ids,memory_ids FROM transmissions").fetchone())
            self.assertEqual((audit["contact_id"], audit["model"], audit["result"]), ("a", "test", "ok"))
            self.assertEqual(len(json.loads(audit["message_ids"])), 4)
            self.assertEqual(len(json.loads(audit["memory_ids"])), 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
