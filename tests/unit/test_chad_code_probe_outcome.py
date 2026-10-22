"""Drive the actual probe CLI path with mocked jobs and real outcome verification."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from evals import chad_code_e2e_probe as probe


class TestProbeOutcome(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Keep these CLI tests offline while checking that production selects Docker.
        original = probe.verify_fixbugs
        def offline(*args, **kwargs):
            self.assertTrue(kwargs.pop("container"))
            return original(*args, **kwargs)
        patcher = patch.object(probe, "verify_fixbugs", side_effect=offline)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("validate_controls", {"ready": True}), ("run_provenance", {"revision": "offline"})):
            patcher = patch.object(probe, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_finished_no_op_and_success_claim_cannot_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mathlib.py").write_text(probe._BUGGY_MATHLIB, encoding="utf-8")
            (root / "test_mathlib.py").write_text(probe._MATHLIB_TESTS, encoding="utf-8")
            result = {"job_id": "offline", "status": "finished", "exit_code": "0",
                      "files": ["mathlib.py", "test_mathlib.py"], "transcript_tail": ["All tests passed!"]}
            with patch("sys.argv", ["probe", "--preset", "fixbugs", "--workspace", str(root), "--poll", "0"]), \
                 patch.object(probe.OpenHandsDispatchTool, "execute", AsyncMock(return_value={
                     "success": True, "result": {"job_id": "offline", "container": "none", "workspace": str(root)}})), \
                 patch.object(probe.CheckCodingTaskTool, "execute", AsyncMock(return_value={"success": True, "result": result})), \
                 patch.object(probe, "_full_logs", return_value="All tests passed!"), \
                 patch.object(probe, "_report_dir", return_value=root):
                code = await probe.main()
            self.assertNotEqual(code, 0)
            report = json.loads((root / "offline.json").read_text())
            self.assertEqual(report["verification"]["outcome"], "no_op")
            self.assertTrue((root / "offline.verification.log").exists())

    async def test_invalid_caller_workspace_never_dispatches(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("sys.argv", ["probe", "--preset", "fixbugs", "--workspace", directory]), \
             patch.object(probe.OpenHandsDispatchTool, "execute", AsyncMock()) as dispatch:
            self.assertNotEqual(await probe.main(), 0)
            dispatch.assert_not_awaited()

    async def test_bad_control_blocks_model_dispatch_and_saves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mathlib.py").write_text(probe._BUGGY_MATHLIB, encoding="utf-8")
            (root / "test_mathlib.py").write_text(probe._MATHLIB_TESTS, encoding="utf-8")
            with patch("sys.argv", ["probe", "--preset", "fixbugs", "--workspace", directory]), \
                 patch.object(probe, "validate_controls", return_value={"ready": False}), \
                 patch.object(probe, "_report_dir", return_value=root), \
                 patch.object(probe.OpenHandsDispatchTool, "execute", AsyncMock()) as dispatch:
                self.assertEqual(await probe.main(), 2)
                dispatch.assert_not_awaited()
            self.assertEqual(json.loads(next(root.glob("preflight-*.json")).read_text())["outcome"], "invalid_fixture_or_grader")

    async def test_actual_fixed_source_produces_verified_report_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mathlib.py").write_text(probe._BUGGY_MATHLIB, encoding="utf-8")
            (root / "test_mathlib.py").write_text(probe._MATHLIB_TESTS, encoding="utf-8")
            fixed = (probe._BUGGY_MATHLIB
                .replace("range(1, n):", "range(1, n + 1):")
                .replace("return True  # bug: 0, 1, and negatives are not prime", "return False")
                .replace("return a / b  # bug: raises ZeroDivisionError, not ValueError, on b == 0",
                         "if b == 0:\n        raise ValueError('zero')\n    return a / b")
                .replace("return b  # bug: returns 0", "return abs(a)")
                .replace("range(n + 1):", "range(n):"))
            async def dispatch(**kwargs):
                (root / "mathlib.py").write_text(fixed, encoding="utf-8")
                return {"success": True, "result": {"job_id": "fixed", "container": "none", "workspace": str(root)}}
            result = {"status": "finished", "exit_code": "0", "files": ["mathlib.py", "test_mathlib.py"]}
            with patch("sys.argv", ["probe", "--preset", "fixbugs", "--workspace", str(root), "--poll", "0"]), \
                 patch.object(probe.OpenHandsDispatchTool, "execute", side_effect=dispatch), \
                 patch.object(probe.CheckCodingTaskTool, "execute", AsyncMock(return_value={"success": True, "result": result})), \
                 patch.object(probe, "_full_logs", return_value="finished"), \
                 patch.object(probe, "_report_dir", return_value=root):
                self.assertEqual(await probe.main(), 0)
            report = json.loads((root / "fixed.json").read_text())
            self.assertEqual(report["verification"]["outcome"], "resolved")
            self.assertIn("Ran 7 tests", (root / "fixed.verification.log").read_text())


if __name__ == "__main__":
    unittest.main()
