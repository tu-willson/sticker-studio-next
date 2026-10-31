import unittest
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


class TextOnlyStickerStyleTests(unittest.TestCase):
    def test_style_is_available_as_its_own_mode_toggle(self):
        self.assertIn('V12_TEXT_ONLY_STYLE = "📝 純文字創意貼圖"', APP)
        self.assertIn('_style_options = ["↓ 請選擇風格"] + _v8_style_items', APP)
        self.assertIn('key="v12_use_text_only_style"', APP)
        self.assertIn('"📝 純文字創意貼圖"', APP)
        step01_index = APP.index('v10_section(t("section_person"), "#ff5c7a")')
        style_index = APP.index('v10_section(t("section_style"), "#ff9f43")')
        text_toggle_index = APP.index('key="v12_use_text_only_style"')
        self.assertLess(step01_index, text_toggle_index)
        self.assertLess(text_toggle_index, style_index)

    def test_text_only_prompt_rejects_people_and_requires_exact_text(self):
        self.assertIn('本次為「純文字創意貼圖」模式，不需人物照片。', APP)
        self.assertIn('嚴禁出現人物、臉部、身體、動物、角色、手勢、日常物件、場景、風景、對話人物或具象插圖。', APP)
        self.assertIn('文字必須完全正確、清楚可讀；不可錯字、漏字、改字、加入額外文字、編號、英數符號或水印。', APP)

    def test_text_only_generation_can_use_a_style_reference_without_a_person_photo(self):
        self.assertIn('if not _text_only_generation and not st.session_state.uploaded_image_bytes:', APP)
        self.assertIn('_generation_client.with_options(timeout=240).images.generate', APP)
        self.assertIn('_generation_client.with_options(timeout=240).images.edit', APP)
        self.assertIn('純文字畫風參考模式', APP)
        self.assertIn('"style-reference.jpg", BytesIO(_style_reference_bytes), "image/jpeg"', APP)
        self.assertIn('_elapsed >= 240', APP)
        self.assertIn('每次生成可重新設計字體的排版、配色、材質、層次與抽象裝飾', APP)

    def test_enabling_text_only_mode_removes_person_input_but_keeps_style_reference_available(self):
        self.assertIn('st.session_state["uploaded_image_bytes"] = None', APP)
        self.assertIn('st.session_state["v12_style_source_mode"] = V12_STYLE_REFERENCE_MODE', APP)
        self.assertIn('可在下方上傳畫風參考', APP)
        self.assertIn('人物照片已強制移除，此模式不接受人物照片。', APP)

    def test_text_reference_priority_suspends_font_and_outline_without_erasing_them(self):
        self.assertIn('key="v12_text_reference_priority"', APP)
        self.assertIn('文字畫風參考優先（使用上傳參考圖的字體意象）', APP)
        self.assertIn('disabled=_text_reference_priority_active', APP)
        self.assertIn('not _text_reference_priority_active', APP)
        self.assertIn('text_reference_priority', APP)
        self.assertIn('【文字畫風參考優先｜嚴格純文字】', APP)
        self.assertIn('嚴禁 3D 立體字、浮雕、描邊、外框、底牌、對話框、投影、光暈', APP)
        self.assertIn('【參考圖文字內嵌效果優先例外】本規則優先於前述禁止效果', APP)
        self.assertIn('只可復刻位於文字本身內部的視覺語言', APP)
        self.assertIn('若參考圖為黑白／墨色，僅使用黑、灰、白與紙色', APP)
        self.assertIn('text_reference_priority=_text_reference_priority_active', APP)
        self.assertIn('✨ 允許自訂補充效果（選填）', APP)
        self.assertIn('【允許自訂補充效果｜唯一例外】', APP)
        self.assertIn('if "style_reference_custom_elements" in data:', APP)
        self.assertIn('raw_style_reference_custom_elements = data.get(', APP)
        self.assertIn('勾選或輸入後會自動套用，並隨作品與創作膠卷保存', APP)
        self.assertIn('key="v12_text_reference_dimensional"', APP)
        self.assertIn('on_change=_v12_capture_style_reference_details_draft', APP)
        self.assertIn('.st-key-v12_text_reference_dimensional [data-testid="stCheckbox"] label p', APP)
        self.assertIn('width:27px !important;', APP)
        self.assertIn('color:#ffe09a !important;', APP)
        self.assertNotIn('（勾選後請點選套用）', APP)

    def test_reel_apply_preserves_text_reference_priority_in_navigation_draft(self):
        self.assertIn(
            'v12_text_reference_priority=bool(\n'
            '            st.session_state.get("v12_text_reference_priority", False)\n'
            '        ),',
            APP,
        )
        self.assertIn('st.session_state["v12_style_reference_strong_choice"] = (', APP)
        self.assertIn('【加入立體感｜保留內嵌效果優先】', APP)
        self.assertIn('加入一眼可辨識的字體厚度或切面，而不是只加平面描邊', APP)
        self.assertIn('每個字至少要有一側連續可見的厚度、側面或立體切面', APP)
        self.assertIn('嚴禁把文字退化成素面白字、通用 3D 字、統一黑色投影', APP)
        self.assertIn('text_reference_dimensional', APP)
        self.assertIn('立體感必須由使用者為新字體重新確認', APP)
        self.assertIn('v12_style_reference_clear_upload_control"] = True', APP)
        self.assertIn('def _v12_style_reference_influence_changed(mode):', APP)
        self.assertIn('v12_style_reference_soft_choice', APP)
        self.assertIn('v12_style_reference_strong_choice', APP)
        self.assertIn(
            'v12_use_text_only_style=bool(\n'
            '            st.session_state.get("v12_use_text_only_style", False)\n'
            '        ),',
            APP,
        )
        self.assertIn(
            'v12_use_text_person_style=bool(\n'
            '            st.session_state.get("v12_use_text_person_style", False)\n'
            '        ),',
            APP,
        )

    def test_generate_recovery_remounts_the_browser_submit_button(self):
        self.assertIn('def _v12_recover_generation_ui_callback()', APP)
        self.assertIn('"↩️ 回復未生成狀態"', APP)
        self.assertIn('v12_frontend_generate_reset_revision', APP)
        self.assertIn('不會清除照片、文字或其他設定', APP)
        self.assertIn('disabled=_generation_locked', APP)
        self.assertIn('絕不能中斷有效生成', APP)

    def test_text_person_mode_keeps_photo_and_makes_text_the_main_subject(self):
        self.assertIn('V12_TEXT_PERSON_STYLE_LABEL = "👤 文字搭人物（字大人小）"', APP)
        self.assertIn('key="v12_use_text_person_style"', APP)
        self.assertIn('text_person=False', APP)
        self.assertIn('文字需占畫面約 60%～75%', APP)
        self.assertIn('人物只能是迷你陪襯，約占畫面 15%～30%', APP)
        self.assertIn('迷你人物必須直接與文字互動', APP)
        self.assertIn('st.session_state["v12_use_text_only_style"] = False', APP)


if __name__ == "__main__":
    unittest.main()
