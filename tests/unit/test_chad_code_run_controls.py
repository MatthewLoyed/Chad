import unittest
from unittest.mock import patch
from pathlib import Path
import json

from evals.chad_code_run_controls import validate_controls, run_provenance


class TestRunControls(unittest.TestCase):
    def test_baseline_test_error_is_valid_but_infrastructure_error_is_not(self):
        for metadata, expected in (({"tests_run": 7, "failures": 4, "errors": 1}, True), ({}, False)):
            with self.subTest(metadata=metadata), patch("evals.chad_code_run_controls.grade_container", side_effect=[
                {"outcome": "verification_error", "metadata": metadata}, {"outcome": "resolved"}]):
                self.assertEqual(validate_controls(b"", b"")["ready"], expected)

    def test_passing_baseline_or_failing_oracle_blocks(self):
        for outcomes in (("resolved", "resolved"), ("tests_failed", "tests_failed")):
            with patch("evals.chad_code_run_controls.grade_container", side_effect=[
                {"outcome": outcomes[0], "metadata": {"tests_run": 1, "failures": 1}}, {"outcome": outcomes[1]}]):
                self.assertFalse(validate_controls(b"", b"")["ready"])

    def test_provenance_never_records_secrets(self):
        data = [{"Image": "sha256:image", "Config": {"Env": ["CHAD_MODEL=qwen", "CHAD_LLM_API_KEY=secret", "TOKEN=secret", "CHAD_TEMPERATURE=0.6"]}}]
        with patch("evals.chad_code_run_controls.subprocess.check_output", side_effect=["revision\n", "", json.dumps(data)]):
            result = run_provenance("worker", Path.cwd(), "task")
        self.assertEqual(result["settings"], {"CHAD_MODEL": "qwen", "CHAD_TEMPERATURE": "0.6"})
        self.assertNotIn("secret", json.dumps(result))
