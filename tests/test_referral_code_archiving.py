import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "supabase/migrations/20261103_000075_archive_referral_codes.sql"
).read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


class ReferralCodeArchivingTests(unittest.TestCase):
    def test_archive_is_audit_preserving_and_requires_prior_deactivation(self):
        self.assertIn("add column if not exists archived_at timestamptz", MIGRATION)
        self.assertIn("and c.is_active = false", MIGRATION)
        self.assertIn("referral_code_must_be_inactive", MIGRATION)
        self.assertIn("where c.archived_at is null", MIGRATION)

    def test_admin_ui_requires_cancel_then_confirmation(self):
        self.assertIn('"🗑️ 取消使用"', APP)
        self.assertIn('disabled=True', APP)
        self.assertIn('"🗑️ 刪除"', APP)
        self.assertIn('"確認刪除"', APP)
        self.assertIn("_beta_admin_archive_referral_code_callback", APP)


if __name__ == "__main__":
    unittest.main()
