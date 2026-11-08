"""Regression checks for cross-browser generation-lock feedback."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8")
FRONTEND = ROOT.joinpath("step04_frontend_test.py").read_text(encoding="utf-8")


class GenerationStatusSyncTests(unittest.TestCase):
    def test_creation_page_does_not_expose_paused_taiwan_backup_link(self):
        """The paid Cloud Run backup is paused and must not be shown publicly."""
        self.assertNotIn("台灣備援創作站（正式站延遲時可改由此創作）", APP)
        self.assertNotIn(
            "https://sticker-studio-next-formal-candidate-335753249693.asia-east1.run.app/",
            APP,
        )

    def test_sync_uses_tiny_status_poll_without_streamlit_rerun(self):
        self.assertIn("GENERATION_STATUS_MONITOR_JS", FRONTEND)
        self.assertIn("get_my_generation_recovery_status", APP)
        self.assertIn(
            "const pollMs = Math.max(10000, Number(data.pollMs) || 15000);",
            FRONTEND,
        )
        self.assertIn("if (!visible()) return;", FRONTEND)
        self.assertIn("此帳號正在其他裝置生成中", FRONTEND)
        self.assertIn("generationStatusKey", FRONTEND)
        self.assertIn("render_frontend_generation_status_monitor", APP)

    def test_stale_local_generation_lock_is_reconciled_with_server_status(self):
        self.assertIn("Always reconcile the browser's transient lock", APP)
        self.assertIn("_server_allows_stale_recovery", APP)
        self.assertIn("_v11_clear_generation_state()", APP)

    def test_apply_handoff_temporarily_locks_apply_and_generate_controls(self):
        self.assertIn("apply-cooldown-until", FRONTEND)
        self.assertIn("Date.now() + 5000", FRONTEND)
        self.assertIn("設定同步中，請稍候", FRONTEND)


if __name__ == "__main__":
    unittest.main()
