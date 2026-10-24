from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "supabase" / "migrations" / "20261024_000057_admin_member_account_governance.sql"
).read_text(encoding="utf-8")
MEMBERSHIP_MIGRATION = (
    ROOT / "supabase" / "migrations" / "20261024_000058_admin_remove_member_entitlements.sql"
).read_text(encoding="utf-8")


class MemberAccountGovernanceTests(unittest.TestCase):
    def test_migration_has_audited_governance_actions(self):
        self.assertIn("create table if not exists public.member_account_admin_actions", MIGRATION)
        for action in (
            "suspend_account",
            "restore_account",
            "suspend_marketplace",
            "restore_marketplace",
            "delete_private_works",
            "delete_account",
        ):
            self.assertIn(f"'{action}'", MIGRATION)

    def test_migration_preserves_buyer_snapshots(self):
        self.assertIn("set status = 'deleted', source_reel_id = null", MIGRATION)
        self.assertIn("Purchasers retain their immutable snapshot", MIGRATION)
        self.assertNotIn("delete from auth.users", MIGRATION.lower())

    def test_app_checks_server_account_access(self):
        self.assertIn('"get_my_account_access"', APP)
        self.assertIn("此帳號目前已被停權，請聯絡客服。", APP)
        self.assertIn("此帳號已由管理員刪除，無法繼續使用。", APP)

    def test_admin_ui_requires_explicit_delete_confirmation(self):
        self.assertIn("輸入 DELETE 以確認", APP)
        self.assertIn("確認執行帳號管理操作", APP)
        self.assertIn("🛡️ 帳號、作品與市集限制", APP)

    def test_membership_removal_preserves_non_paid_credit_sources(self):
        self.assertIn("移除會員資格（回復免費會員）", APP)
        self.assertIn("source_kind in ('subscription', 'credit_pack')", MEMBERSHIP_MIGRATION)
        self.assertIn("推薦碼、基本體驗／管理員補次與市集創作者回饋不受影響", APP)


if __name__ == "__main__":
    unittest.main()
