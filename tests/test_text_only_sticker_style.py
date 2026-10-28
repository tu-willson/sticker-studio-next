import unittest
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


class TextOnlyStickerStyleTests(unittest.TestCase):
    def test_style_is_available_in_the_preset_dropdown(self):
        self.assertIn('V12_TEXT_ONLY_STYLE = "📝 純文字創意貼圖（隨機）"', APP)
        self.assertIn('_style_options = ["↓ 請選擇風格", V12_TEXT_ONLY_STYLE]', APP)

    def test_text_only_prompt_rejects_people_and_requires_exact_text(self):
        self.assertIn('本次為「純文字創意貼圖」模式，不需人物照片。', APP)
        self.assertIn('嚴禁出現人物、臉部、身體、動物、角色、手勢、日常物件、場景、風景、對話人物或具象插圖。', APP)
        self.assertIn('文字必須完全正確、清楚可讀；不可錯字、漏字、改字、加入額外文字、編號、英數符號或水印。', APP)

    def test_text_only_generation_uses_prompt_only_image_api(self):
        self.assertIn('if not _text_only_generation and not st.session_state.uploaded_image_bytes:', APP)
        self.assertIn('_generation_client.with_options(timeout=180).images.generate', APP)
        self.assertIn('每次生成可重新設計字體的排版、配色、材質、層次與抽象裝飾', APP)


if __name__ == "__main__":
    unittest.main()
