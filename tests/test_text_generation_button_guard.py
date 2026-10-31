import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8")
FRONTEND = ROOT.joinpath("step04_frontend_test.py").read_text(encoding="utf-8")


class TextGenerationButtonGuardTests(unittest.TestCase):
    def test_text_mode_without_content_disables_both_generation_buttons(self):
        self.assertIn('_text_generation_missing_content = (', APP)
        self.assertIn('_sticker_mode == "text"', APP)
        self.assertIn('disabled=_generation_locked or _text_generation_missing_content', APP)
        self.assertIn('「使用文字貼圖」模式，至少填入一格文字。', APP)

    def test_browser_generator_uses_the_current_local_text_draft(self):
        self.assertIn('const textModeEmpty = Boolean(', FRONTEND)
        self.assertIn("state.mode === 'text'", FRONTEND)
        self.assertIn("slot => String(slot?.text || '').trim()", FRONTEND)
        self.assertIn('const disabled = Boolean(data.disabled || textModeEmpty);', FRONTEND)
        self.assertIn('class="step04-generator-note"', FRONTEND)


if __name__ == "__main__":
    unittest.main()
