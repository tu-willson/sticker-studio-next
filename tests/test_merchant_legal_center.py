import unittest
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


class MerchantLegalCenterTests(unittest.TestCase):
    def test_public_policy_centre_identifies_the_registered_member(self):
        self.assertIn('V12_PUBLIC_SITE_URL = "https://sticker-studio-next.streamlit.app"', APP)
        self.assertIn('🎨LINE 貼圖創作室', APP)
        self.assertIn('商店會員：<strong>tuwillson</strong>', APP)
        self.assertIn('隱私權保護政策', APP)

    def test_policy_centre_covers_privacy_refunds_and_support(self):
        self.assertIn('退款與取消政策', APP)
        self.assertIn('TUWILLSON@GMAIL.COM', APP)
        self.assertIn('https://www.threads.com/@tuwillson', APP)
        self.assertIn('信用卡卡號等付款敏感資料由金流服務商依其政策處理', APP)

    def test_pricing_page_uses_the_verified_brand_and_email(self):
        self.assertIn('服務聯絡／金流商店 驗證信箱', APP)
        self.assertIn('<div class="v12-pricing-kicker">🎨LINE 貼圖創作室</div>', APP)


if __name__ == "__main__":
    unittest.main()
