import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "supabase/migrations/20261027_000063_free_member_library_capacity_two.sql"
).read_text(encoding="utf-8")


class FreeMemberLibraryCapacityTests(unittest.TestCase):
    def test_database_free_entitlement_has_two_project_capacity(self):
        self.assertIn("'free'::text, 2, 2, null::timestamptz", MIGRATION)

    def test_member_rules_describe_two_free_projects(self):
        self.assertIn("雲端作品庫 <strong>2 組</strong>", APP)
        self.assertIn("免費會員預設可保存 2 組作品", APP)
        self.assertIn("Free members can save 2 projects", APP)
        self.assertIn("無料会員は2作品", APP)

    def test_app_fallbacks_match_the_free_capacity(self):
        self.assertIn('response.get("library_capacity", 2) or 2', APP)
        self.assertGreaterEqual(APP.count('slot.get("library_capacity", 2) or 2'), 2)


if __name__ == "__main__":
    unittest.main()
