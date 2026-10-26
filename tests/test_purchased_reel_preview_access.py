"""Static safeguards for purchased-reel preview access after moderation."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20261026_000060_preserve_purchased_reel_previews_after_moderation.sql"
)


class PurchasedReelPreviewAccessTests(unittest.TestCase):
    def test_purchaser_access_is_not_limited_to_style_reference_assets(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("t.preview_path = p_name or t.style_reference_path = p_name", sql)
        self.assertIn("from public.shared_template_redemptions r", sql)
        self.assertIn("r.consumer_id = auth.uid()", sql)
        self.assertNotIn(
            "t.style_reference_path = p_name\n          and exists", sql
        )

    def test_withdrawn_reels_are_not_made_public(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("t.status = 'published' and t.preview_path = p_name", sql)
        self.assertIn("grant execute on function public.shared_template_asset_is_readable(text) to authenticated", sql)


if __name__ == "__main__":
    unittest.main()
