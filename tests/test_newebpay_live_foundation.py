import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20261102_000068_newebpay_live_checkout_foundation.sql").read_text(encoding="utf-8")
ONE_TIME_MIGRATION = (ROOT / "supabase/migrations/20261102_000069_one_time_creator_packs.sql").read_text(encoding="utf-8")
CHECKOUT = (ROOT / "supabase/functions/newebpay-checkout/index.ts").read_text(encoding="utf-8")
NOTIFY = (ROOT / "supabase/functions/newebpay-notify/index.ts").read_text(encoding="utf-8")
RETURN = (ROOT / "supabase/functions/newebpay-return/index.ts").read_text(encoding="utf-8")
PERIODIC_NOTIFY = (ROOT / "supabase/functions/newebpay-periodic-notify/index.ts").read_text(encoding="utf-8")
PERIODIC_RETURN = (ROOT / "supabase/functions/newebpay-periodic-return/index.ts").read_text(encoding="utf-8")
CONFIG = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")


class NewebPayLiveFoundationTests(unittest.TestCase):
    def test_only_service_role_can_finalize_and_new_model_has_independent_lots(self):
        self.assertIn("grant execute on function public.finalize_newebpay_initial_payment", MIGRATION)
        self.assertIn("grant execute on function public.finalize_newebpay_initial_payment", ONE_TIME_MIGRATION)
        self.assertIn("'one_time_30_day_pack'", ONE_TIME_MIGRATION)
        self.assertIn("get_my_active_paid_credit_lots", ONE_TIME_MIGRATION)
        self.assertIn("v_end timestamptz := now() + interval '30 days'", ONE_TIME_MIGRATION)

    def test_checkout_remains_dark_launched_and_wallets_default_off(self):
        self.assertIn("NEWEBPAY_LIVE_ENABLED", CHECKOUT)
        self.assertIn("!LIVE_ENABLED", CHECKOUT)
        self.assertIn("NEWEBPAY_WALLETS_ENABLED", CHECKOUT)
        self.assertIn('APPLEPAY: WALLETS_ENABLED ? "1" : "0"', CHECKOUT)
        self.assertIn('ANDROIDPAY: WALLETS_ENABLED ? "1" : "0"', CHECKOUT)
        self.assertIn('SAMSUNGPAY: "0"', CHECKOUT)

    def test_all_new_orders_use_the_one_time_mpg_endpoint(self):
        self.assertIn("https://core.newebpay.com/MPG/mpg_gateway", CHECKOUT)
        self.assertNotIn("https://core.newebpay.com/MPG/period", CHECKOUT)
        self.assertNotIn("PeriodType", CHECKOUT)
        self.assertNotIn("isPeriodic", CHECKOUT)
        self.assertIn("'mpg'", ONE_TIME_MIGRATION)
        self.assertNotIn("subscription_plan_not_upgrade", ONE_TIME_MIGRATION)

    def test_callbacks_are_verified_and_return_pages_never_issue_entitlements(self):
        self.assertIn("newebpayTradeSha", NOTIFY)
        self.assertIn("decryptNewebpayMpg", NOTIFY)
        self.assertIn("reconcileVerifiedRecentOrders", NOTIFY)
        self.assertIn("finalize_newebpay_initial_payment", NOTIFY)
        self.assertIn("decryptNewebpayMpg", RETURN)
        self.assertIn("record_newebpay_periodic_renewal", PERIODIC_NOTIFY)
        self.assertNotIn("finalize_newebpay_initial_payment", RETURN)
        self.assertNotIn("record_newebpay_periodic_renewal", PERIODIC_RETURN)

    def test_payment_return_popup_closes_without_recreating_member_session(self):
        self.assertIn("def _v12_close_payment_return_popup", APP)
        self.assertIn('target="newebpay_checkout"', APP)
        self.assertIn("const isPaymentReturn = params.has('payment_order');", APP)
        self.assertIn("const isNamedCheckout = popup.name === 'newebpay_checkout';", APP)
        self.assertIn("if (!isPaymentReturn || (!isNamedCheckout && !popup.opener)) return;", APP)
        self.assertIn("opener.focus();", APP)
        self.assertIn("opener.location.reload();", APP)
        self.assertIn("popup.close()", APP)
        self.assertIn("_v12_close_payment_return_popup()", APP)

    def test_all_callback_functions_allow_provider_posts_without_jwt(self):
        for name in (
            "newebpay-checkout", "newebpay-notify", "newebpay-return",
            "newebpay-periodic-notify", "newebpay-periodic-return",
        ):
            self.assertIn(f"[functions.{name}]", CONFIG)


if __name__ == "__main__":
    unittest.main()
