from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "supabase" / "migrations" / "20261024_000059_member_custom_style_bank.sql"
).read_text(encoding="utf-8")


class MemberCustomStyleBankTests(unittest.TestCase):
    def test_migration_keeps_style_banks_private_per_member(self):
        self.assertIn("create table if not exists public.member_custom_style_banks", MIGRATION)
        self.assertIn("user_id uuid primary key references auth.users(id)", MIGRATION)
        self.assertIn("enable row level security", MIGRATION)
        self.assertIn("revoke all on table public.member_custom_style_banks", MIGRATION)

    def test_migration_exposes_only_authenticated_member_rpcs(self):
        self.assertIn("function public.get_my_custom_style_bank()", MIGRATION)
        self.assertIn("function public.save_my_custom_style_bank(p_style_bank jsonb)", MIGRATION)
        self.assertIn("v_user uuid := auth.uid()", MIGRATION)
        self.assertIn("grant execute on function public.get_my_custom_style_bank() to authenticated", MIGRATION)

    def test_app_loads_after_login_and_saves_after_style_edit(self):
        self.assertIn('"get_my_custom_style_bank"', APP)
        self.assertIn('"save_my_custom_style_bank"', APP)
        self.assertIn("_v12_sync_member_custom_style_bank(session)", APP)
        self.assertIn("_beta_save_member_custom_style_bank(session, bank)", APP)


if __name__ == "__main__":
    unittest.main()
