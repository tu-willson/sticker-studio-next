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
        expected = {
            "軟陶公仔", "紙黏土手作", "黏土定格動畫風",
            "日系可愛插畫", "韓系簡約插畫", "貼紙插畫風",
            "復古電動遊戲風",
        }
        self.assertTrue(expected.issubset(guides))
        self.assertIn("【此風格強化規則】", SOURCE)


if __name__ == "__main__":
    unittest.main()
