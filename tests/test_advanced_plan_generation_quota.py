"""Guard the Advanced plan's published and enforced generation allowance."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20261026_000061_advanced_plan_45_monthly_generations.sql"
).read_text(encoding="utf-8")


class AdvancedPlanGenerationQuotaTests(unittest.TestCase):
    def test_catalog_grants_forty_five_generations_for_future_periods(self):
        self.assertIn("set image_credits = 45", MIGRATION)
        self.assertIn("where code = 'advanced_monthly'", MIGRATION)

    def test_all_member_facing_advanced_generation_copy_is_forty_five(self):
        self.assertIn("| 進階方案 | NT$199／月 | 45 次 | 25 次 | 40 組 |", APP)
        self.assertIn("| Advanced | NT$199/month | 45 | 25 | 40 |", APP)
        self.assertIn("| アドバンス | NT$199／月 | 45回 | 25回 | 40組 |", APP)
        self.assertIn("每月圖片生成 20／30／45 次", APP)
        self.assertIn("進階方案｜圖片生成 45 次", APP)
        self.assertIn('t("benefit_credits", count=45', APP)


if __name__ == "__main__":
    unittest.main()
