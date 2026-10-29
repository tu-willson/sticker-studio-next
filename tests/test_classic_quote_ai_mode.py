import unittest
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
EDITOR = (Path(__file__).resolve().parents[1] / "step04_frontend_test.py").read_text(
    encoding="utf-8"
)


class ClassicQuoteAiModeTests(unittest.TestCase):
    def test_mode_is_available_in_the_backend_and_browser_editor(self):
        self.assertIn('V12_AI_COPY_CLASSIC_QUOTE_MODE = "🎬 經典台詞"', APP)
        self.assertIn('V12_AI_COPY_MODES = (', APP)
        self.assertIn("const AI_IDEA_MODES = [", EDITOR)
        self.assertIn('data-root-field="aiMode"', EDITOR)

    def test_mode_generates_original_homage_not_verbatim_copyrighted_dialogue(self):
        self.assertIn('原創致敬式短句', APP)
        self.assertIn('絕對不要逐字重現、拼接、改寫得過於接近', APP)
        self.assertIn('不逐字重現台詞、歌詞或招牌口號', EDITOR)

    def test_classic_quote_mode_disables_and_ignores_tone_selection(self):
        self.assertIn('tone = "依人物／作品氣質自動延伸（不套用語氣模式）"', APP)
        self.assertIn('disabled=True,', APP)
        self.assertIn('依人物／作品氣質自動延伸', EDITOR)
        self.assertIn('語氣模式已停用', EDITOR)

    def test_browser_event_carries_mode_to_generation(self):
        self.assertIn('"ideaMode": payload.get("aiMode", V12_AI_COPY_STANDARD_MODE)', APP)
        self.assertIn('_v12_ai_idea_mode', APP)
        self.assertIn('_v12_ai_copy_generate(', APP)


if __name__ == "__main__":
    unittest.main()
