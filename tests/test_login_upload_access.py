"""Exercise login/upload controls without calling production authentication."""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest


APP_TREE = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8"))
HELPERS = ast.Module(
    body=[node for node in APP_TREE.body if isinstance(node, ast.FunctionDef)
          and node.name == "_beta_image_uploader"],
    type_ignores=[],
)


class LoginUploadAccessTests(unittest.TestCase):
    def helpers(self, streamlit):
        scope = {"st": streamlit, "t": lambda key: key}
        exec(compile(HELPERS, "app.py", "exec"), scope)
        return scope

    def test_anonymous_stale_upload_is_not_returned_for_processing(self):
        st = Mock()
        st.file_uploader.return_value = object()
        uploader = self.helpers(st)["_beta_image_uploader"]
        self.assertIsNone(uploader("photo", session=None, key="person"))
        self.assertTrue(st.file_uploader.call_args.kwargs["disabled"])

    def test_authenticated_upload_is_returned(self):
        st = Mock()
        upload = object()
        st.file_uploader.return_value = upload
        result = self.helpers(st)["_beta_image_uploader"](
            "reference", session={"user": {"id": "test-user"}}, key="reference"
        )
        self.assertIs(result, upload)
        self.assertFalse(st.file_uploader.call_args.kwargs["disabled"])
        st.caption.assert_not_called()

    def test_rendered_image_inputs_require_login(self):
        for authenticated in (False, True):
            with self.subTest(authenticated=authenticated):
                script = "import streamlit as st\nimport html\nt = lambda key: key\n"
                script += ast.unparse(HELPERS)
                script += f"\nsession = {{'user': {{'id': 'test'}}}} if {authenticated} else None\n"
                script += "_beta_image_uploader('person', session=session, key='person')\n"
                script += "_beta_image_uploader('reference', session=session, key='reference')\n"
                at = AppTest.from_string(script).run()
                self.assertEqual(len(at.exception), 0)
                uploads = at.get("file_uploader")
                self.assertEqual(len(uploads), 2)
                self.assertEqual([item.proto.disabled for item in uploads], [not authenticated] * 2)


if __name__ == "__main__":
    unittest.main()
