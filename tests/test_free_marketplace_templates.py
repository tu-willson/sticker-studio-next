import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
MIGRATION = ROOT / "supabase" / "migrations" / "20261103_000073_allow_free_marketplace_templates.sql"
CREATIVE_TEXT_TAG_MIGRATION = (
    ROOT / "supabase" / "migrations" / "20261103_000074_add_creative_text_marketplace_tag.sql"
)


class FreeMarketplaceTemplatesTests(unittest.TestCase):
    def test_publish_form_can_choose_a_free_listing(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("options=[0, 1, 2, 3]", source)
        self.assertIn("🛍️ 免費套用膠卷", source)
        self.assertNotIn("cost=max(1, int(template.get(\"apply_cost\"", source)

    def test_database_allows_zero_and_converts_live_listings(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("check (apply_cost between 0 and 3)", sql)
        self.assertIn("set apply_cost = 0\nwhere status = 'published';", sql)
        self.assertIn("p_apply_cost not between 0 and 3", sql)
        self.assertIn("if v_charge > 0 then", sql)

    def test_style_examples_and_payment_reconcile_button_are_updated(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("https://www.threads.com/share/EzlgQ8CSh/", source)
        self.assertIn("示範連結", source)
        self.assertIn("width:140px !important", source)
        self.assertNotIn('key="v12_newebpay_reconcile"', source)

    def test_creative_text_is_a_server_validated_marketplace_tag(self):
        source = APP.read_text(encoding="utf-8")
        sql = CREATIVE_TEXT_TAG_MIGRATION.read_text(encoding="utf-8")
        self.assertIn('"創意文字"', source)
        self.assertIn("'創意文字'", sql)
        self.assertIn("v12-reel-action-notice", source)
        self.assertIn('("message", "details", "hint", "code")', source)
        self.assertIn('"shared_templates_marketplace_required"', source)
        self.assertIn("active_publication_count", source)


if __name__ == "__main__":
    unittest.main()
