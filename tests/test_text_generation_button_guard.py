import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8")
FRONTEND = ROOT.joinpath("step04_frontend_test.py").read_text(encoding="utf-8")


class TextGenerationButtonGuardTests(unittest.TestCase):
    def test_text_mode_shows_a_step_six_reminder_without_locking_the_button(self):
        self.assertIn('v10_section(t("section_generate"), "#e67e22")', APP)
        self.assertIn('if _sticker_mode == "text":', APP)
        self.assertIn('「使用文字貼圖」模式，至少填入一格文字，避免送出失敗。', APP)
        self.assertIn('.v12-text-generation-reminder', APP)
        self.assertIn('disabled=_generation_locked,', APP)
        self.assertNotIn('_text_generation_missing_content', APP)

    def test_browser_generator_uses_the_current_local_text_draft(self):
        self.assertIn('const isTextModeEmpty = (state) => Boolean(', FRONTEND)
        self.assertIn("state.mode === 'text'", FRONTEND)
        self.assertIn("slot => String(slot?.text || '').trim()", FRONTEND)
        self.assertIn('const disabled = Boolean(data.disabled);', FRONTEND)
        self.assertIn("button.addEventListener('click', () => {", FRONTEND)
        self.assertIn('const state = readDraft();', FRONTEND)
        self.assertIn('if (isTextModeEmpty(state)) return;', FRONTEND)

    def test_browser_generator_does_not_capture_a_stale_draft_before_click(self):
        generator = FRONTEND.split('GENERATOR_JS = r"""', 1)[1].split('"""', 1)[0]
        before_click, click_handler = generator.split("button.addEventListener('click', () => {", 1)
        self.assertNotIn('const state = readDraft();', before_click)
        self.assertIn('const state = readDraft();', click_handler)


if __name__ == "__main__":
    unittest.main()
