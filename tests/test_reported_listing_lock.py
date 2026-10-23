"""Static safeguards for the three-report marketplace lock migration."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20261024_000054_lock_reported_listing_republication.sql"
APP = ROOT / "app.py"


class ReportedListingLockTests(unittest.TestCase):
    def test_database_enforces_the_republication_lock(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("moderation_republish_blocked boolean", sql)
        self.assertIn("shared_template_moderation_queue_lock_listing", sql)
        self.assertIn("shared_templates_prevent_locked_republication", sql)
        self.assertIn("new.resolution = 'withdraw_listing'", sql)
        self.assertIn("shared_template_republication_blocked", sql)

    def test_publisher_can_read_the_lock_and_review_reason(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("get_my_shared_template_moderation_status", source)
        self.assertIn("shared_template_republish_blocked_reason", source)
        self.assertIn("管理者審核原因", source)


if __name__ == "__main__":
    unittest.main()
