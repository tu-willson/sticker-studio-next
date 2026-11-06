"""Regression checks for cross-browser generation-lock feedback."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8")
FRONTEND = ROOT.joinpath("step04_frontend_test.py").read_text(encoding="utf-8")


class GenerationStatusSyncTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
