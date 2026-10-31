import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")


class BasketClearConfirmationTests(unittest.TestCase):
    def test_clear_button_requires_a_second_deliberate_click(self):
        self.assertIn("def _v12_clear_sticker_basket_callback():", APP)
        self.assertIn('"v12_basket_clear_confirmation_pending"', APP)
        self.assertIn('st.session_state["v12_sticker_basket"] = []', APP)
        self.assertIn('t("basket_clear_confirm") if _basket_clear_pending', APP)

    def test_clear_is_scoped_to_workspace_images(self):
        self.assertIn('st.session_state["v12_sticker_basket_batch_number"] = 0', APP)
        self.assertIn('st.success(t("basket_clear_success"))', APP)
        self.assertIn('project["project_id"] = str(uuid4())', APP)
        self.assertIn('st.session_state.pop("v12_loaded_cloud_project_id", None)', APP)
        self.assertNotIn('def _v12_clear_sticker_basket_callback():\n    """Require a second deliberate click before clearing only workspace images."""\n    st.session_state["uploaded_image_bytes"]', APP)

    def test_successful_save_keeps_an_overwrite_identity_until_workspace_clear(self):
        self.assertIn('"project_id": project_id,', APP)
        self.assertIn('st.session_state["v12_loaded_cloud_project_id"] = str(', APP)
        self.assertIn('if str(exc) != "beta_request_failed_http_409":', APP)

    def test_save_rules_use_distinct_new_and_overwrite_note_colours(self):
        self.assertIn("v12-cloud-save-note-new-work", APP)
        self.assertIn("v12-cloud-save-note-overwrite", APP)


if __name__ == "__main__":
    unittest.main()
