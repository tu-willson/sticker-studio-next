import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "supabase/migrations/20261029_000068_paid_member_referral_codes.sql"
).read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


class PaidMemberReferralCodeTests(unittest.TestCase):
    def test_referral_codes_store_the_paid_member_requirement(self):
        self.assertIn("requires_paid_membership boolean not null default false", MIGRATION)
        self.assertIn("p_requires_paid_membership boolean", MIGRATION)

    def test_paid_member_eligibility_uses_successful_commerce_orders(self):
        self.assertIn("o.status = 'paid'", MIGRATION)
        self.assertIn("p.kind in ('subscription', 'credit_pack')", MIGRATION)
        self.assertIn("referral_paid_member_required", MIGRATION)

    def test_paid_member_codes_do_not_block_normal_referral_combination(self):
        self.assertIn("if not v_code.requires_paid_membership and exists", MIGRATION)
        self.assertIn("and not prior_code.requires_paid_membership", MIGRATION)

    def test_admin_ui_exposes_the_requirement_and_specific_error(self):
        self.assertIn("beta_admin_referral_requires_paid_membership", APP)
        self.assertIn('"paid_member_required"', APP)
        self.assertIn("可與一般推薦碼獎勵併存", APP)


if __name__ == "__main__":
    unittest.main()
