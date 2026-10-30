import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.joinpath("app.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _literal_assignment(name):
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing assignment: {name}")


class StyleCatalogCurationTests(unittest.TestCase):
    def test_retired_styles_are_not_selectable(self):
        styles = _literal_assignment("V8_STYLES")
        for retired in (
            "毛氈玩偶",
            "Q版黏土3D",
            "3D收藏公仔",
            "奶油霧面3D",
            "透明背景貼圖風",
        ):
            self.assertNotIn(retired, styles)
        self.assertIn("復古電動遊戲風", styles)

    def test_old_style_snapshots_have_safe_fallbacks(self):
        fallbacks = _literal_assignment("V12_RETIRED_STYLE_FALLBACKS")
        self.assertEqual(fallbacks["毛氈玩偶"], "羊毛氈手作")
        self.assertEqual(fallbacks["透明背景貼圖風"], "貼紙插畫風")

    def test_close_style_groups_and_retro_game_have_prompt_guides(self):
        guides = _literal_assignment("V12_STYLE_PROMPT_GUIDES")
        selectable = {
            style for style in _literal_assignment("V8_STYLES")
            if not style.startswith("⭐")
        }
        self.assertEqual(set(guides), selectable)
        self.assertIn("【此風格強化規則】", SOURCE)

    def test_every_selectable_style_has_a_preview_asset_and_category(self):
        styles = {
            style for style in _literal_assignment("V8_STYLES")
            if not style.startswith("⭐")
        }
        catalog = _literal_assignment("V12_STYLE_PREVIEW_CATALOG")
        self.assertEqual(set(catalog), styles)
        preview_dir = ROOT / "static" / "style_previews"
        for _category, asset in catalog.values():
            self.assertTrue((preview_dir / asset).is_file(), asset)
        self.assertIn("快速瀏覽 35 種貼圖風格", SOURCE)
        self.assertIn('background:transparent !important;', SOURCE)

    def test_style_and_font_gallery_controls_have_distinct_hover_colors(self):
        self.assertIn(".st-key-v12_style_gallery_toggle button", SOURCE)
        self.assertIn("rgba(245,180,52,.34)", SOURCE)
        self.assertIn(".st-key-v10_font_gallery_toggle button", SOURCE)
        self.assertIn("rgba(141,103,216,.30)", SOURCE)

    def test_font_guidance_is_aligned_to_each_font_subsection(self):
        self.assertIn(".v12-font-caption", SOURCE)
        self.assertIn("white-space:pre-line", SOURCE)
        self.assertIn('v12_font_caption(t("font_outline_caption"))', SOURCE)
        self.assertIn('v12_font_caption(t("font_pick_caption"))', SOURCE)

    def test_text_outline_catalog_has_previews_in_requested_order(self):
        expected = [
            "不使用外框",
            "塗鴉手繪外框",
            "雲朵對話泡泡框",
            "漫畫爆炸對話框",
            "雙層撞色描邊",
            "愛心泡泡外框",
            "彩虹波浪外框",
            "金屬徽章外框",
            "鋸齒票券外框",
            "速度線聚焦外框",
            "圓角膠囊標籤框",
            "火焰爆裂外框",
        ]
        options = _literal_assignment("V8_TEXT_STYLE_OPTIONS")
        labels = _literal_assignment("V12_TEXT_OUTLINE_LABELS")
        previews = _literal_assignment("V12_TEXT_OUTLINE_PREVIEW_CATALOG")
        preview_dir = ROOT / "static" / "text_outline_previews"

        self.assertEqual(options, expected)
        self.assertNotIn("厚白貼紙描邊", options)
        self.assertNotIn("厚白貼紙描邊", labels)
        self.assertEqual(list(previews), expected[1:])
        for asset in previews.values():
            self.assertTrue((preview_dir / asset).is_file(), asset)
        self.assertIn('key="v12_outline_gallery_toggle"', SOURCE)
        self.assertIn('key="v12_outline_card_grid"', SOURCE)
        self.assertIn('background:transparent !important;', SOURCE)

    def test_main_step_guidance_is_aligned_to_its_section_title(self):
        self.assertIn(".v12-section-caption", SOURCE)
        for translation_key in (
            "photo_intro",
            "style_intro",
            "features_intro",
            "content_intro",
        ):
            self.assertIn(f'v12_section_caption(t("{translation_key}"))', SOURCE)


if __name__ == "__main__":
    unittest.main()
