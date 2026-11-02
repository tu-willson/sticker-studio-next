import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
I18N = (ROOT / "i18n.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20261102_000069_one_time_creator_packs.sql").read_text(encoding="utf-8")


class OneTimeCreatorPackTests(unittest.TestCase):
    def test_all_paid_products_are_described_as_one_time_30_day_packs(self):
        self.assertIn("one_time_30_day_pack", MIGRATION)
        self.assertIn("'basic_monthly', 'standard_monthly', 'advanced_monthly', 'credit_pack_7'", MIGRATION)
        self.assertIn("單次購買・30 天效期", I18N)
        self.assertIn("不會自動續訂或定期扣款", APP)

    def test_tier_rights_and_credit_only_pack_are_separate(self):
        self.assertIn("when 'advanced_monthly' then 3", MIGRATION)
        self.assertIn("7-credit refill remains a credit-only product", MIGRATION)
        self.assertIn("get_my_creator_reel_library_status", MIGRATION)
        self.assertIn("只增加圖片生成次數，不提升方案權益", I18N)

    def test_pricing_does_not_lock_repeat_purchases_or_upgrades(self):
        pricing = APP[APP.index("def _v12_render_pricing_page"):]
        self.assertNotIn("subscription_ranks =", pricing)
        self.assertIn('disabled=False', pricing)
        self.assertIn('"period": t("period_creator_pack")', pricing)


if __name__ == "__main__":
    unittest.main()
