"""Regression coverage for the expression-only STEP 04 mode handoff."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (ROOT / "step04_frontend_test.py").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


class ExpressionModeTextToolsTests(unittest.TestCase):
    def test_mode_change_is_sent_to_streamlit_immediately(self):
        self.assertIn(
            "setTriggerValue('event', {kind:'mode-change', state});",
            FRONTEND,
        )

    def test_server_snapshot_maps_expression_mode(self):
        self.assertIn('payload.get("mode") == "expression"', APP)
        self.assertIn("V12_STICKER_EXPRESSION_MODE", APP)


if __name__ == "__main__":
    unittest.main()
