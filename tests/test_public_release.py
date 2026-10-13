import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loveshe.config import config_path, model_settings, public_model_info, save_settings
from loveshe.core import call_model, open_db
from loveshe.demo import seed


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("LOVESHE_")}
        clean["LOVESHE_DATA_DIR"] = str(self.root)
        env = patch.dict(os.environ, clean, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_fresh_demo_is_isolated_idempotent_and_preview_only(self):
        examples = Path(__file__).resolve().parent.parent / "examples"
        seed(self.root, examples)
        seed(self.root, examples)
        db = open_db(self.root)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 10)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 2)
        with patch("urllib.request.urlopen") as network, patch("subprocess.run") as cli:
            preview = call_model(db, "demo-a", "advise", "怎么回复？")
            self.assertEqual(preview["model"], "UNCONFIGURED")
            with self.assertRaisesRegex(ValueError, "尚未配置"):
                call_model(db, "demo-a", "advise", "怎么回复？", preview_only=False)
            network.assert_not_called()
            cli.assert_not_called()
        self.assertFalse(public_model_info()["configured"])

    def test_private_config_override_and_no_key_in_public_info(self):
        settings = {"backend": "api", "api_base": "https://example.invalid/v1",
                    "model": "fictional-model", "api_key": "fictional-test-secret"}  # pragma: allowlist secret -- fictional fixture
        save_settings(settings)
        self.assertEqual(model_settings(), settings)
        self.assertTrue(config_path().parent.samefile(self.root))
        self.assertNotIn(settings["api_key"], json.dumps(public_model_info()))
        self.assertTrue(public_model_info()["configured"])
        with patch.dict(os.environ, {"LOVESHE_MODEL": "different-model"}):
            self.assertEqual(model_settings()["model"], "different-model")

    def test_invalid_configuration_does_not_replace_existing(self):
        settings = {"backend": "codex", "api_base": "", "model": "fictional-model", "api_key": ""}
        save_settings(settings)
        # pragma: allowlist nextline secret -- invalid credential URL test
        for base in ("https://user:secret@example.invalid/v1", "https://example.invalid/v1?key=secret", "http://example.invalid/v1"):
            with self.assertRaises(ValueError):
                save_settings({**settings, "backend": "api", "api_key": "fictional-secret", "api_base": base})  # pragma: allowlist secret -- fictional fixture
        self.assertEqual(model_settings(), settings)
        config_path().write_text("invalid-json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "配置无法读取"):
            model_settings()

    def test_release_files_reject_private_artifacts(self):
        from scripts.check_release import allowed, check
        for name in ("data/pilot.sqlite3", "private/report.md", ".env", "loveshe/model_config.json", "docs/screenshot.jpg"):
            self.assertFalse(allowed(Path(name)), name)
        self.assertTrue(allowed(Path("examples/demo-a.json")))
        # Use the source allowlist but never read the developer's data directory.
        files = check()
        self.assertFalse(any(p.parts[0] in {"data", "private", ".upstream"} for p in files))


if __name__ == "__main__":
    unittest.main()
