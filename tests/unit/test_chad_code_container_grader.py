import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evals.chad_code_container_grader import grade_container


class TestContainerGrader(unittest.TestCase):
    def test_restricted_command_and_structured_counts(self):
        report = dict(outcome="resolved", tests_run=2, failures=0, errors=0, output="OK")
        proc = SimpleNamespace(returncode=0, stdout="CHAD_GRADE_JSON:"+json.dumps(report), stderr="")
        with patch("evals.chad_code_container_grader.subprocess.run", return_value=proc) as run:
            result = grade_container(b"VALUE=1", b"", 10)
        self.assertEqual(result["outcome"], "resolved")
        args = run.call_args_list[0].args[0]
        for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--pull=never", "--user=65534:65534"):
            self.assertIn(flag, args)
        self.assertEqual(result["metadata"]["tests_run"], 2)
        self.assertEqual(run.call_args_list[-1].args[0][1:3], ["rm", "-f"])

    def test_no_structured_result_cannot_pass_even_exit_zero(self):
        proc = SimpleNamespace(returncode=0, stdout="All tests passed!", stderr="")
        with patch("evals.chad_code_container_grader.subprocess.run", return_value=proc):
            self.assertEqual(grade_container(b"", b"", 10)["outcome"], "verification_error")

    def test_timeout_keeps_output_and_removes_container(self):
        with patch("evals.chad_code_container_grader.subprocess.run", side_effect=[
            subprocess.TimeoutExpired("docker", 1, output=b"partial", stderr=b"error"), None]) as run:
            result = grade_container(b"", b"", 1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stdout"], "partial")
        self.assertEqual(run.call_args_list[-1].args[0][1:3], ["rm", "-f"])

    def test_inconsistent_structured_result_cannot_pass(self):
        report = dict(outcome="resolved", tests_run=0, failures=0, errors=0, output="")
        proc = SimpleNamespace(returncode=0, stdout="CHAD_GRADE_JSON:"+json.dumps(report), stderr="")
        with patch("evals.chad_code_container_grader.subprocess.run", return_value=proc):
            self.assertEqual(grade_container(b"", b"", 10)["outcome"], "verification_error")
