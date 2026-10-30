import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")


class TextLedModesProjectSnapshotTests(unittest.TestCase):
    def test_modes_are_kept_in_cloud_and_reel_metadata(self):
        self.assertIn('"text_only_style", "text_person_style",', APP)
        self.assertIn('"text_only_style": False,', APP)
        self.assertIn('"text_person_style": False,', APP)
        self.assertIn('project["text_only_style"] = bool(', APP)
        self.assertIn('project["text_person_style"] = bool(', APP)

    def test_loading_a_snapshot_restores_both_mode_toggles(self):
        self.assertIn('st.session_state["v12_use_text_only_style"] = bool(', APP)
        self.assertIn('st.session_state["v12_use_text_person_style"] = bool(', APP)
        self.assertIn('text_person_style = False', APP)

    def test_template_transfer_contract_carries_text_led_modes(self):
        self.assertIn('"text_only_style": bool(snapshot.get("text_only_style", False))', APP)
        self.assertIn('"text_person_style": bool(snapshot.get("text_person_style", False))', APP)

    def test_project_name_inputs_are_mirrored_across_fragment_reruns(self):
        self.assertIn('def _v12_project_name_top_on_change()', APP)
        self.assertIn('st.session_state["v12_project_name_input"] = value', APP)
        self.assertIn('st.session_state["v12_project_name_top_input"] = value', APP)
        self.assertIn('const findInputs = () =>', APP)
        self.assertIn('const mirror = (source, value) =>', APP)
        self.assertIn('input.addEventListener(\'input\', saveAndMirror)', APP)


if __name__ == "__main__":
    unittest.main()
