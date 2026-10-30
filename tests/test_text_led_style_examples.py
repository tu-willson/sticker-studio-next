import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8")


class TextLedStyleExamplesTests(unittest.TestCase):
    def test_both_examples_are_shipped_with_the_app(self):
        self.assertTrue(ROOT.joinpath("static/style_examples/text-only.png").is_file())
        self.assertTrue(
            ROOT.joinpath(
                "static/style_examples/text-person-large-text-small-person.png"
            ).is_file()
        )

    def test_creation_page_displays_both_examples(self):
        self.assertIn('static/style_examples/text-only.png', APP)
        self.assertIn(
            'static/style_examples/text-person-large-text-small-person.png', APP
        )
        self.assertIn('st.image("static/style_examples/text-only.png", width=94)', APP)
        self.assertIn('width=94,', APP)
        self.assertIn('background:transparent !important;', APP)


if __name__ == "__main__":
    unittest.main()
