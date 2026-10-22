"""Outcome tests execute the independent grader; no model or Docker involved."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evals.chad_code_verification import (
    capture_fixbugs_fixture, verification_exit_code, verify_fixbugs,
)

BUGGY = b"VALUE = 0\n"
FIXED = b"VALUE = 1\n"
TESTS = b"import unittest, mathlib\nclass Tests(unittest.TestCase):\n def test_value(self): self.assertEqual(mathlib.VALUE, 1)\n"


class TestVerification(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        (self.workspace / "mathlib.py").write_bytes(BUGGY)
        (self.workspace / "test_mathlib.py").write_bytes(TESTS)

    def verify(self, status="finished", exit_code="0"):
        return verify_fixbugs(self.workspace, BUGGY, TESTS, status, exit_code)

    def test_original_buggy_files_are_no_op_even_when_worker_finishes(self):
        result = self.verify()
        self.assertEqual(result.outcome, "no_op")
        self.assertNotEqual(verification_exit_code("finished", result), 0)

    def test_real_fix_passes_authoritative_tests(self):
        (self.workspace / "mathlib.py").write_bytes(FIXED)
        result = self.verify()
        self.assertEqual(result.outcome, "resolved", result)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Ran 1 test", result.stderr)
        self.assertEqual(verification_exit_code("finished", result), 0)

    def test_changed_but_still_broken_fails(self):
        (self.workspace / "mathlib.py").write_bytes(b"VALUE = 2\n")
        self.assertEqual(self.verify().outcome, "tests_failed")

    def test_modified_tests_never_grade_themselves(self):
        (self.workspace / "mathlib.py").write_bytes(FIXED)
        (self.workspace / "test_mathlib.py").write_bytes(b"# tests passed!\n")
        self.assertEqual(self.verify().outcome, "tests_modified")

    def test_missing_file_is_verification_error(self):
        for name in ("mathlib.py", "test_mathlib.py"):
            with self.subTest(name=name):
                path = self.workspace / name
                original = path.read_bytes()
                path.unlink()
                self.assertEqual(self.verify().outcome, "verification_error")
                path.write_bytes(original)

    def test_import_error_is_verification_error(self):
        (self.workspace / "mathlib.py").write_bytes(b"raise ImportError('broken source')\n")
        result = self.verify()
        self.assertEqual(result.outcome, "verification_error")
        self.assertIn("ImportError", result.stderr)

    def test_timeout_is_not_success(self):
        (self.workspace / "mathlib.py").write_bytes(FIXED)
        with patch("evals.chad_code_verification.subprocess.run", side_effect=subprocess.TimeoutExpired("test", 1)):
            result = self.verify()
        self.assertEqual(result.outcome, "verification_error")
        self.assertTrue(result.timed_out)

    def test_backend_failure_or_poll_timeout_do_not_grade(self):
        with patch("evals.chad_code_verification.subprocess.run") as run:
            self.assertEqual(self.verify(exit_code="1").outcome, "backend_error")
            self.assertEqual(self.verify(status="failed").outcome, "backend_error")
            self.assertEqual(self.verify(status="running").outcome, "timeout")
        run.assert_not_called()

    def test_fresh_grade_ignores_worker_import_hooks(self):
        (self.workspace / "mathlib.py").write_bytes(FIXED)
        (self.workspace / "unittest.py").write_text("raise RuntimeError('hijacked')")
        (self.workspace / "sitecustomize.py").write_text("raise RuntimeError('hijacked')")
        self.assertEqual(self.verify().outcome, "resolved")

    def test_validate_caller_fixture_before_dispatch(self):
        self.assertEqual(capture_fixbugs_fixture(self.workspace, TESTS), (BUGGY, TESTS))
        (self.workspace / "test_mathlib.py").write_bytes(b"# unrelated tests")
        with self.assertRaisesRegex(ValueError, "authoritative"):
            capture_fixbugs_fixture(self.workspace, TESTS)

    def test_windows_newlines_validate_but_exact_test_bytes_are_protected(self):
        windows = TESTS.replace(b"\n", b"\r\n")
        (self.workspace / "test_mathlib.py").write_bytes(windows)
        self.assertEqual(capture_fixbugs_fixture(self.workspace, TESTS), (BUGGY, windows))

    def test_unrelated_presets_keep_completion_exit_behavior(self):
        self.assertEqual(verification_exit_code("finished", None), 0)
        self.assertNotEqual(verification_exit_code("running", None), 0)


if __name__ == "__main__":
    unittest.main()
