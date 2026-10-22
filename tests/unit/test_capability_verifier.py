"""
Unit tests for Stage-6 capability verification + the gated skill-writing foundation.

These cover the PURE / decision logic that needs no live E2B sandbox or LLM:
  * the SkillCraft-style `is_nontrivial_result` heuristic,
  * `extract_result` parsing of the sandbox stdout marker,
  * the CapabilityVerifier verdict + fail-open mapping (sandbox/draft mocked),
  * the flag gating (default OFF),
  * skill-name sanitization + generated-code validity,
  * the token gate that makes an ungated skill write impossible.
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.capability_verifier import (
    RESULT_MARKER,
    CapabilityVerifier,
    build_capability_verify_fn,
    capability_verify_enabled,
    extract_result,
    is_nontrivial_result,
)
from src.core.intelligence import capability_skill_builder as builder


def run(coro):
    return asyncio.run(coro)


class TestNonTrivialHeuristic(unittest.TestCase):
    def test_genuine_result_passes(self):
        self.assertTrue(is_nontrivial_result(
            success=True, error=None, stderr="", result_value="42 degrees, sunny"))

    def test_failed_run_fails(self):
        self.assertFalse(is_nontrivial_result(
            success=False, error=None, stderr="", result_value="something"))

    def test_explicit_error_fails(self):
        self.assertFalse(is_nontrivial_result(
            success=True, error="boom", stderr="", result_value="something"))

    def test_traceback_on_stderr_fails(self):
        self.assertFalse(is_nontrivial_result(
            success=True, error=None,
            stderr="Traceback (most recent call last):\n  File ...", result_value="x"))

    def test_missing_result_fails(self):
        self.assertFalse(is_nontrivial_result(
            success=True, error=None, stderr="", result_value=None))

    def test_trivial_stub_values_fail(self):
        for stub in ["", "None", "null", "Unknown", "0", "0.0", "[]", "{}",
                     "TODO", "not implemented", "  N/A  ", "'None'", '"unknown"']:
            with self.subTest(stub=stub):
                self.assertFalse(is_nontrivial_result(
                    success=True, error=None, stderr="", result_value=stub))

    def test_punctuation_only_fails(self):
        self.assertFalse(is_nontrivial_result(
            success=True, error=None, stderr="", result_value="---!!!"))

    def test_nonzero_number_passes(self):
        self.assertTrue(is_nontrivial_result(
            success=True, error=None, stderr="", result_value="42"))


class TestExtractResult(unittest.TestCase):
    def test_extracts_marker_line(self):
        out = f"noise\n{RESULT_MARKER} hello world\nmore noise"
        self.assertEqual(extract_result(out), "hello world")

    def test_last_occurrence_wins(self):
        out = f"{RESULT_MARKER} first\n{RESULT_MARKER} second"
        self.assertEqual(extract_result(out), "second")

    def test_no_marker_returns_none(self):
        self.assertIsNone(extract_result("just some output"))

    def test_empty_returns_none(self):
        self.assertIsNone(extract_result(""))


class TestCapabilityVerifierVerdict(unittest.TestCase):
    def _verifier(self, *, code, run_result, fail_open=True):
        async def draft(_cand):
            return code

        async def sandbox(_code):
            return run_result

        return CapabilityVerifier(draft_fn=draft, sandbox_run_fn=sandbox, fail_open=fail_open)

    def test_verified_true_on_nontrivial(self):
        v = self._verifier(code="print('x')",
                            run_result={"success": True, "error": None, "stderr": "",
                                        "stdout": f"{RESULT_MARKER} 73 F"})
        self.assertTrue(run(v.verify({"title": "t"})))

    def test_real_negative_false_on_trivial(self):
        v = self._verifier(code="print('x')",
                            run_result={"success": True, "error": None, "stderr": "",
                                        "stdout": f"{RESULT_MARKER} None"})
        self.assertFalse(run(v.verify({"title": "t"})))

    def test_unverified_none_when_sandbox_down(self):
        v = self._verifier(code="print('x')", run_result=None)  # sandbox unavailable
        self.assertIsNone(run(v.verify({"title": "t"})))

    def test_unverified_none_when_draft_empty(self):
        v = self._verifier(code=None, run_result={"success": True})
        self.assertIsNone(run(v.verify({"title": "t"})))

    def test_fail_open_keeps_candidate_when_unverified(self):
        # sandbox down + fail_open=True -> verify_fn returns True (surface anyway, like today)
        v = self._verifier(code="print('x')", run_result=None, fail_open=True)
        self.assertTrue(run(v.as_verify_fn()({"title": "t"})))

    def test_fail_closed_drops_candidate_when_unverified(self):
        v = self._verifier(code="print('x')", run_result=None, fail_open=False)
        self.assertFalse(run(v.as_verify_fn()({"title": "t"})))

    def test_real_negative_drops_regardless_of_fail_open(self):
        v = self._verifier(code="print('x')",
                            run_result={"success": True, "error": None, "stderr": "",
                                        "stdout": f"{RESULT_MARKER} 0"},
                            fail_open=True)
        self.assertFalse(run(v.as_verify_fn()({"title": "t"})))


class TestFlagGating(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CHAD_CAPABILITY_VERIFY")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CHAD_CAPABILITY_VERIFY", None)
        else:
            os.environ["CHAD_CAPABILITY_VERIFY"] = self._saved

    def test_default_off(self):
        os.environ.pop("CHAD_CAPABILITY_VERIFY", None)
        self.assertFalse(capability_verify_enabled())
        self.assertIsNone(build_capability_verify_fn())

    def test_explicit_on(self):
        for val in ["1", "true", "YES", "on"]:
            with self.subTest(val=val):
                os.environ["CHAD_CAPABILITY_VERIFY"] = val
                self.assertTrue(capability_verify_enabled())
                self.assertIsNotNone(build_capability_verify_fn())

    def test_off_values(self):
        for val in ["0", "false", "", "no"]:
            with self.subTest(val=val):
                os.environ["CHAD_CAPABILITY_VERIFY"] = val
                self.assertFalse(capability_verify_enabled())


class TestSkillNameSanitization(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(builder.sanitize_skill_name("Send Telegram Message!!"),
                         "send_telegram_message")

    def test_leading_digit_prefixed(self):
        self.assertEqual(builder.sanitize_skill_name("3d render"), "skill_3d_render")

    def test_collapses_separators(self):
        self.assertEqual(builder.sanitize_skill_name("foo---bar  baz"), "foo_bar_baz")

    def test_rejects_unusable_names(self):
        # Names that reduce to nothing usable must raise rather than write to a junk path.
        for bad in ["...", "!!!", "  ", "", "/", "@@@"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    builder.sanitize_skill_name(bad)

    def test_path_separators_neutralized_not_traversal(self):
        # Separators collapse to underscores, so traversal-looking input becomes a flat,
        # safe local skill name (never escapes src/skills/).
        self.assertEqual(builder.sanitize_skill_name("../../etc"), "etc")
        name = builder.sanitize_skill_name("foo/../bar")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)


class TestGeneratedCodeValidity(unittest.TestCase):
    def test_agent_py_compiles(self):
        cand = {"title": "Weather Lookup", "description": 'gets "weather"',
                "rationale": "users keep asking", "confidence": 0.9, "cluster_size": 4}
        src = builder.render_skill_agent(cand, "weather_lookup")
        compile(src, "weather_lookup/agent.py", "exec")  # raises SyntaxError on failure
        self.assertIn("class WeatherLookupSkill(BaseSkill)", src)
        self.assertIn('name = "weather_lookup"', src)

    def test_triple_quote_in_description_neutralized(self):
        cand = {"title": "x", "description": 'a """ injection', "rationale": "r"}
        src = builder.render_skill_agent(cand, "x_skill")
        compile(src, "x_skill/agent.py", "exec")


class TestTokenGate(unittest.TestCase):
    """The core safety property: no write without a valid one-time token."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_skills_root"
        # Clean slate
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_commit_refuses_unknown_token(self):
        res = run(builder.commit_skill_write("CHAD-DEAD", registry=None))
        self.assertFalse(res["success"])
        self.assertIn("Invalid or expired", res["error"])

    def test_commit_refuses_foreign_token(self):
        # A token registered for a different purpose (e.g. a file plan) must not authorize a write.
        from src.tools.filesystem.file_manager import generate_approval_token, register_approval
        tok = generate_approval_token()
        register_approval(tok, {"kind": "file_plan_not_skill", "actions": []})
        res = run(builder.commit_skill_write(tok, registry=None))
        self.assertFalse(res["success"])
        self.assertIn("does not authorize", res["error"])

    def test_propose_writes_nothing_to_disk(self):
        cand = {"title": "Disk Untouched", "description": "d", "rationale": "r"}
        out = builder.propose_skill_write(cand, skills_root=str(self.tmp))
        self.assertTrue(out["success"])
        self.assertTrue(out["requires_approval"])
        self.assertFalse(self.tmp.exists(), "propose_skill_write must not touch disk")
        self.assertIn("approval_token", out)

    def test_full_happy_path_writes_only_after_token(self):
        cand = {"title": "Happy Path", "description": "does a thing", "rationale": "r",
                "confidence": 0.9, "cluster_size": 3}
        out = builder.propose_skill_write(cand, skills_root=str(self.tmp))
        token = out["approval_token"]
        target = Path(out["target_dir"])
        self.assertFalse(target.exists())

        res = run(builder.commit_skill_write(token, registry=None))
        self.assertTrue(res["success"], res)
        self.assertTrue((target / "agent.py").exists())
        self.assertTrue((target / "SKILL.md").exists())
        # registry=None -> re-scan skipped, but write still succeeded
        self.assertFalse(res["rescanned"])
        # The written agent.py is valid python
        compile((target / "agent.py").read_text(encoding="utf-8"), "agent.py", "exec")

    def test_token_is_one_time(self):
        cand = {"title": "Once Only", "description": "d", "rationale": "r"}
        out = builder.propose_skill_write(cand, skills_root=str(self.tmp))
        token = out["approval_token"]
        first = run(builder.commit_skill_write(token, registry=None))
        self.assertTrue(first["success"])
        second = run(builder.commit_skill_write(token, registry=None))  # token already consumed
        self.assertFalse(second["success"])

    def test_rescan_called_with_live_registry(self):
        """When a registry is supplied, commit triggers a SkillScanner re-scan against it."""
        cand = {"title": "Rescan Me", "description": "d", "rationale": "r"}
        out = builder.propose_skill_write(cand, skills_root=str(self.tmp))

        scanned = {}

        class FakeRegistry:
            def register_tool(self, *a, **k):
                pass

        # Patch SkillScanner.scan_directory to observe the call without importing real skills.
        from src.infrastructure.tools.discovery import skill_scanner as ss

        orig = ss.SkillScanner.scan_directory
        def fake_scan(self, directory_path="src/skills"):
            scanned["dir"] = directory_path
        ss.SkillScanner.scan_directory = fake_scan
        try:
            res = run(builder.commit_skill_write(out["approval_token"], registry=FakeRegistry()))
        finally:
            ss.SkillScanner.scan_directory = orig

        self.assertTrue(res["success"])
        self.assertTrue(res["rescanned"])
        self.assertEqual(scanned.get("dir"), str(self.tmp))


if __name__ == "__main__":
    unittest.main()
