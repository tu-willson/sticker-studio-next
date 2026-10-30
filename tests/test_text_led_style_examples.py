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
        self.assertIn('static/style_examples/', APP)
        self.assertIn('text-person-large-text-small-person.png', APP)
        self.assertIn("V12_TEXT_ONLY_STYLE_CHECKBOX_LABEL", APP)
        self.assertIn("V12_TEXT_PERSON_STYLE_CHECKBOX_LABEL", APP)
        self.assertIn('height:34px !important;', APP)
        self.assertNotIn('st.image("static/style_examples/text-only.png", width=94)', APP)


if __name__ == "__main__":
    unittest.main()
