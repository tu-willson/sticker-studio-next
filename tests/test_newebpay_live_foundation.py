import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20261102_000068_newebpay_live_checkout_foundation.sql").read_text(encoding="utf-8")
CHECKOUT = (ROOT / "supabase/functions/newebpay-checkout/index.ts").read_text(encoding="utf-8")
NOTIFY = (ROOT / "supabase/functions/newebpay-notify/index.ts").read_text(encoding="utf-8")
RETURN = (ROOT / "supabase/functions/newebpay-return/index.ts").read_text(encoding="utf-8")
PERIODIC_NOTIFY = (ROOT / "supabase/functions/newebpay-periodic-notify/index.ts").read_text(encoding="utf-8")
PERIODIC_RETURN = (ROOT / "supabase/functions/newebpay-periodic-return/index.ts").read_text(encoding="utf-8")
CONFIG = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")


class NewebPayLiveFoundationTests(unittest.TestCase):
    def test_only_service_role_can_finalize_or_renew(self):
        self.assertIn("grant execute on function public.finalize_newebpay_initial_payment", MIGRATION)
        self.assertIn("grant execute on function public.record_newebpay_periodic_renewal", MIGRATION)
        self.assertIn("to service_role;", MIGRATION)
        self.assertIn("revoke all on function public.finalize_newebpay_initial_payment", MIGRATION)

    def test_checkout_remains_dark_launched_and_wallets_default_off(self):
        self.assertIn("NEWEBPAY_LIVE_ENABLED", CHECKOUT)
        self.assertIn("!LIVE_ENABLED", CHECKOUT)
        self.assertIn("NEWEBPAY_WALLETS_ENABLED", CHECKOUT)
        self.assertIn('APPLEPAY: WALLETS_ENABLED ? "1" : "0"', CHECKOUT)
        self.assertIn('ANDROIDPAY: WALLETS_ENABLED ? "1" : "0"', CHECKOUT)
        self.assertIn('SAMSUNGPAY: "0"', CHECKOUT)

    def test_payment_types_use_the_correct_live_endpoints(self):
        self.assertIn("https://core.newebpay.com/MPG/mpg_gateway", CHECKOUT)
        self.assertIn("https://core.newebpay.com/MPG/period", CHECKOUT)
        self.assertIn('PeriodType: "D", PeriodPoint: "30"', CHECKOUT)
        self.assertIn('PeriodStartType: "2"', CHECKOUT)

    def test_callbacks_are_verified_and_return_pages_never_issue_entitlements(self):
        self.assertIn("newebpayTradeSha", NOTIFY)
        self.assertIn("finalize_newebpay_initial_payment", NOTIFY)
        self.assertIn("record_newebpay_periodic_renewal", PERIODIC_NOTIFY)
        self.assertNotIn("finalize_newebpay_initial_payment", RETURN)
        self.assertNotIn("record_newebpay_periodic_renewal", PERIODIC_RETURN)

    def test_all_callback_functions_allow_provider_posts_without_jwt(self):
        for name in (
            "newebpay-checkout", "newebpay-notify", "newebpay-return",
            "newebpay-periodic-notify", "newebpay-periodic-return",
        ):
            self.assertIn(f"[functions.{name}]", CONFIG)


if __name__ == "__main__":
    unittest.main()
