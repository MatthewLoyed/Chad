"""Unit tests for Canvas LMS URL normalization and config helpers."""

import unittest

from src.tools.canvas.manager import (
    build_canvas_user_agent,
    normalize_canvas_api_base_url,
)


class TestCanvasManagerHelpers(unittest.TestCase):
    def test_normalize_strips_trailing_slash(self):
        self.assertEqual(
            normalize_canvas_api_base_url("https://school.instructure.com/"),
            "https://school.instructure.com/api/v1",
        )

    def test_normalize_preserves_existing_api_path(self):
        self.assertEqual(
            normalize_canvas_api_base_url("https://school.instructure.com/api/v1"),
            "https://school.instructure.com/api/v1",
        )

    def test_normalize_rejects_empty(self):
        self.assertIsNone(normalize_canvas_api_base_url(""))

    def test_user_agent_uses_contact_email(self):
        self.assertEqual(
            build_canvas_user_agent("student@example.com"),
            "ChadAssistant/1.0 (student@example.com)",
        )

    def test_user_agent_default_email(self):
        self.assertEqual(
            build_canvas_user_agent(""),
            "ChadAssistant/1.0 (user@example.com)",
        )


if __name__ == "__main__":
    unittest.main()
