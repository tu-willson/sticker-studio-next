import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
I18N = (ROOT / "i18n.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20261102_000069_one_time_creator_packs.sql").read_text(encoding="utf-8")
REEL_MIGRATION = (ROOT / "supabase/migrations/20261102_000070_universal_reels_and_pack7_entitlements.sql").read_text(encoding="utf-8")


class OneTimeCreatorPackTests(unittest.TestCase):
    def test_all_paid_products_are_described_as_one_time_30_day_packs(self):
        self.assertIn("one_time_30_day_pack", MIGRATION)
        self.assertIn("'basic_monthly', 'standard_monthly', 'advanced_monthly', 'credit_pack_7'", MIGRATION)
        self.assertIn("單次購買・30 天效期", I18N)
        self.assertIn("不會自動續訂或定期扣款", APP)

    def test_tier_rights_include_the_compact_seven_generation_pack(self):
        self.assertIn("when 'advanced_monthly' then 3", MIGRATION)
        self.assertIn("when 'credit_pack_7' then 0", REEL_MIGRATION)
        self.assertIn("daily_copy_limit = 7", REEL_MIGRATION)
        self.assertIn("library_capacity = 7", REEL_MIGRATION)
        self.assertIn("v_collected_limit := 7; v_publish_limit := 3", REEL_MIGRATION)
        self.assertIn('"copy": t("benefit_copy", count=7)', APP)

    def test_uncollected_reels_are_universal_and_roll_for_fifteen_days(self):
        self.assertIn("v_auto_limit integer := 20", REEL_MIGRATION)
        self.assertIn("v_auto_limit, 15, v_collected_limit", REEL_MIGRATION)
        self.assertIn("interval '15 days'", REEL_MIGRATION)
        self.assertIn("超過 20 卷時，系統直接覆蓋最早的未收藏膠卷", APP)

    def test_allowance_rules_are_visible_without_signing_in(self):
        self.assertIn("def _v12_render_member_rules_expander", APP)
        self.assertIn("_v12_render_member_rules_expander()\n    return None", APP)

    def test_pricing_does_not_lock_repeat_purchases_or_upgrades(self):
        pricing = APP[APP.index("def _v12_render_pricing_page"):]
        self.assertNotIn("subscription_ranks =", pricing)
        self.assertIn('disabled=False', pricing)
        self.assertIn('"period": t("period_creator_pack")', pricing)


if __name__ == "__main__":
    unittest.main()
