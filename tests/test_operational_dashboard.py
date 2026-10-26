"""Static safeguards for the private operational event and dashboard layer."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20261026_000062_operational_events_and_admin_dashboard.sql"
).read_text(encoding="utf-8")


class OperationalDashboardTests(unittest.TestCase):
    def test_event_table_is_private_and_limits_event_categories(self):
        self.assertIn("create table if not exists public.operational_events", MIGRATION)
        self.assertIn("alter table public.operational_events enable row level security", MIGRATION)
        self.assertIn("revoke all on table public.operational_events from anon, authenticated", MIGRATION)
        for event_type in (
            "'login'", "'generation'", "'cloud_save'", "'creator_reel_save'",
            "'referral_redemption'", "'marketplace_purchase'", "'payment'",
        ):
            self.assertIn(event_type, MIGRATION)

    def test_database_captures_core_transaction_events(self):
        for trigger_name in (
            "generation_jobs_capture_operational_event",
            "library_projects_capture_operational_event",
            "creator_reels_capture_operational_event",
            "referral_redemptions_capture_operational_event",
            "shared_template_redemptions_capture_operational_event",
            "commerce_orders_capture_operational_event",
        ):
            self.assertIn(trigger_name, MIGRATION)

    def test_admin_queries_are_server_authorized(self):
        for function_name in (
            "admin_get_operational_dashboard",
            "admin_list_operational_trend",
            "admin_list_recent_operational_failures",
        ):
            self.assertIn(function_name, MIGRATION)
            self.assertIn(f'"{function_name}"', APP)
        self.assertIn("if not public.is_beta_admin()", MIGRATION)

    def test_dashboard_is_a_separate_admin_section(self):
        self.assertIn("📊 全站營運儀表板", APP)
        self.assertIn("🧭 會員、市集與規則管理", APP)
        self.assertIn("_beta_render_operational_dashboard(session)", APP)
        self.assertIn("st.line_chart(", APP)

    def test_login_and_save_failures_are_best_effort(self):
        self.assertIn("def _beta_record_login_once", APP)
        self.assertIn("library_{save_stage}_failed", APP)
        self.assertIn("creator_reel_{stage}_failed", APP)
        self.assertIn("Monitoring must never make a creation, save, or login fail.", APP)


if __name__ == "__main__":
    unittest.main()
