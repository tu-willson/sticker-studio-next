import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20261028_000064_payuni_sandbox_checkout.sql").read_text(encoding="utf-8")
CHECKOUT = (ROOT / "supabase/functions/payuni-checkout/index.ts").read_text(encoding="utf-8")
NOTIFY = (ROOT / "supabase/functions/payuni-notify/index.ts").read_text(encoding="utf-8")
RETURN = (ROOT / "supabase/functions/payuni-return/index.ts").read_text(encoding="utf-8")
CONFIG = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")


class PayuniSandboxFoundationTests(unittest.TestCase):
    def test_only_verified_callback_can_finalize_payment(self):
        self.assertIn("revoke all on function public.finalize_payuni_payment", MIGRATION)
        self.assertRegex(
            MIGRATION,
            r"grant execute on function public\.finalize_payuni_payment\([^;]+\)\s+to service_role;",
        )
        self.assertNotRegex(
            MIGRATION,
            r"grant execute on function public\.finalize_payuni_payment\([^;]+\)\s+to authenticated;",
        )

    def test_pending_order_uses_an_idempotent_checkout_session(self):
        self.assertIn("unique (user_id, idempotency_key)", MIGRATION)
        self.assertIn("payuni_checkout_sessions", MIGRATION)
        self.assertIn("perform pg_advisory_xact_lock", MIGRATION)
        self.assertIn("on table public.payuni_checkout_sessions to service_role", MIGRATION)

    def test_checkout_is_sandbox_and_forces_credit_card_only(self):
        self.assertIn("https://sandbox-api.payuni.com.tw/api/upp", CHECKOUT)
        self.assertIn("Credit: 1", CHECKOUT)
        self.assertIn("NotifyURL", CHECKOUT)
        self.assertIn("ReturnURL", CHECKOUT)

    def test_callbacks_verify_hash_and_do_not_grant_from_return_url(self):
        self.assertIn("constantTimeEqual", NOTIFY)
        self.assertIn("finalize_payuni_payment", NOTIFY)
        self.assertIn("constantTimeEqual", RETURN)
        self.assertNotIn("finalize_payuni_payment", RETURN)

    def test_callback_functions_do_not_require_browser_jwt(self):
        self.assertIn("[functions.payuni-notify]", CONFIG)
        self.assertIn("[functions.payuni-return]", CONFIG)
        self.assertEqual(CONFIG.count("verify_jwt = false"), 3)

    def test_checkout_does_not_join_through_auth_users(self):
        self.assertIn('.from("profiles")', CHECKOUT)
        self.assertNotIn("profiles!inner", CHECKOUT)


if __name__ == "__main__":
    unittest.main()
