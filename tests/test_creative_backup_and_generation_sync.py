"""Regression checks for the Taiwan creative-backup safeguards."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8")
FRONTEND = ROOT.joinpath("step04_frontend_test.py").read_text(encoding="utf-8")
ENTRYPOINT = ROOT.joinpath("docker-entrypoint.sh").read_text(encoding="utf-8")


class CreativeBackupAndGenerationSyncTests(unittest.TestCase):
    def test_backup_mode_routes_members_to_formal_purchase_page(self):
        self.assertIn("def _v12_creative_backup_mode_enabled():", APP)
        self.assertIn("def _v12_formal_purchase_url():", APP)
        self.assertIn("https://sticker-studio-next.streamlit.app/?view=pricing", APP)
        self.assertIn("台灣創作備援站", APP)
        self.assertIn("🛒 前往正式站購買／加值", APP)
        self.assertIn("if chosen and not _v12_creative_backup_mode_enabled():", APP)
        self.assertIn("or _v12_creative_backup_mode_enabled()", APP)
        self.assertIn('"V12_CREATIVE_BACKUP_MODE"', ENTRYPOINT)
        self.assertIn('"V12_FORMAL_PURCHASE_URL"', ENTRYPOINT)

    def test_generation_sync_only_polls_a_small_browser_status_value(self):
        self.assertIn("GENERATION_STATUS_MONITOR_JS", FRONTEND)
        self.assertIn("get_my_generation_recovery_status", APP)
        self.assertIn("const pollMs = Math.max(10000, Number(data.pollMs) || 15000);", FRONTEND)
        self.assertIn("if (!visible()) return;", FRONTEND)
        self.assertIn("此帳號正在其他裝置生成中", FRONTEND)
        self.assertIn("generationStatusKey", FRONTEND)
        self.assertIn("render_frontend_generation_status_monitor", APP)


if __name__ == "__main__":
    unittest.main()
