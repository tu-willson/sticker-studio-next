"""Keep crop guides on the inline v2 component runtime used by the live editor."""

from pathlib import Path
import unittest


APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


class CropGuidesInlineComponentTests(unittest.TestCase):
    def test_crop_bridge_uses_v2_inline_assets(self):
        self.assertIn("import streamlit.components.v2 as components_v2", APP)
        self.assertIn('"v12_crop_guides_inline"', APP)
        self.assertIn("root._v12CropSetTrigger?.('save', message.value);", APP)

    def test_crop_render_passes_inline_component_data(self):
        self.assertIn('"applyRequestToken": str(', APP)
        self.assertIn('getattr(_crop_component_result, "save", None)', APP)


if __name__ == "__main__":
    unittest.main()
