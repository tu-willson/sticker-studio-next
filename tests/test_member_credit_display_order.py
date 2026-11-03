import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")


class MemberCreditDisplayOrderTests(unittest.TestCase):
    def test_ai_quota_is_rendered_with_credit_priority_and_has_an_icon(self):
        account_panel = APP[APP.index("def _beta_render_account_panel"):APP.index("def _supabase_rpc")]
        self.assertIn('t(\'account_credit_priority\')', account_panel)
        self.assertIn("copy_quota_html", account_panel)
        self.assertIn('✨ ', account_panel)
        self.assertIn(
            '+ f"<p class=\\"v12-credit-priority\\">{t(\'account_credit_priority\')}</p>"\n'
            '                + copy_quota_html',
            account_panel,
        )


if __name__ == "__main__":
    unittest.main()
