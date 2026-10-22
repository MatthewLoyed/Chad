"""
capability_verifier.py — Stage 6: objective execute-and-check for capability suggestions.

A drafted capability suggestion (from capability_gaps / capability_patterns) is just an
LLM's *claim* that a tool/skill is worth building. Asking another LLM "does this look
good?" is the obvious next step, and the obvious trap: LLM-as-judge has a documented
false-positive bias toward its own proposals (Voyager / SkillCraft both report it). The
durable signal is OBJECTIVE: can a minimal version of the proposed capability actually
RUN and PRODUCE a non-trivial result?

So the verifier does, per candidate:
  1. DRAFT   — a small judge writes a minimal self-contained Python implementation of the
               proposed tool/skill PLUS a tiny self-test that exercises it and prints a
               single RESULT line. (Reuses evals.judges, same as the analyzers' draft_fn.)
  2. EXECUTE — that code runs in the E2B sandbox (run_sandboxed_python). Isolated, no host
               side effects.
  3. JUDGE   — a SkillCraft-style quality heuristic (`is_nontrivial_result`, PURE): the run
               must complete without error AND its printed RESULT must not be an empty /
               None / "Unknown" / 0 stub. Only then → True.

Degradation contract (matches the rest of the self-improvement code — best-effort, never
raises into the turn path):
  * Flag OFF (default) → verifier is never built; analyzers keep `verify_fn=None` and behave
    EXACTLY as today.
  * Sandbox unavailable / E2B down / draft failed → returns `None` ("unverified"), NOT
    False. The analyzer treats a falsy return as "drop", so `None` would drop the candidate —
    therefore the public `verify_fn` maps "unverified" to the configured fail-open default
    (surface anyway, like today) rather than silently swallowing every suggestion when E2B
    is dead. The objective check only ever *removes* a candidate on an explicit negative
    (`is_nontrivial_result` == False after a clean run).

Enable with env `CHAD_CAPABILITY_VERIFY=1`. Judge model via `CHAD_CAPABILITY_JUDGE_MODEL`
(default `qwen/qwen3.5-9b`, same as the analyzers).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

logger = logging.getLogger("CapabilityVerifier")

# Strings that mean "the code ran but produced a placeholder / stub / empty answer".
# Lower-cased, stripped of surrounding punctuation before comparison.
_TRIVIAL_TOKENS = {
    "", "none", "null", "nan", "n/a", "na", "unknown", "undefined",
    "todo", "tbd", "not implemented", "notimplemented", "placeholder",
    "0", "0.0", "[]", "{}", "()", "false",
}

# The sentinel the generated self-test is asked to print, so we can isolate the
# meaningful output from incidental stdout noise.
RESULT_MARKER = "CAPABILITY_VERIFY_RESULT:"


def extract_result(stdout: str) -> Optional[str]:
    """Pull the value the self-test printed after RESULT_MARKER (last occurrence wins).

    Returns None if the marker was never printed (treated as "no result"). Pure.
    """
    if not stdout:
        return None
    found: Optional[str] = None
    for line in stdout.splitlines():
        idx = line.find(RESULT_MARKER)
        if idx >= 0:
            found = line[idx + len(RESULT_MARKER):].strip()
    return found


def is_nontrivial_result(
    *,
    success: bool,
    error: Optional[str],
    stderr: Optional[str],
    result_value: Optional[str],
) -> bool:
    """SkillCraft-style quality heuristic — PURE, the heart of the objective check.

    A drafted capability is considered VERIFIED only if ALL hold:
      * the sandbox run reported success (no transport/exec failure),
      * the code raised no error and emitted no traceback on stderr,
      * a RESULT value was actually printed, and
      * that value is not a trivial/empty/stub placeholder (None/0/"Unknown"/...).

    Returns True only on a genuine, non-trivial result; False otherwise. Never raises.
    """
    if not success:
        return False
    if error:
        return False
    if stderr and ("Traceback (most recent call last)" in stderr or "Error" in stderr):
        return False
    if result_value is None:
        return False

    norm = result_value.strip().strip("\"'").strip().lower()
    # collapse internal whitespace so 'not  implemented' == 'not implemented'
    norm = re.sub(r"\s+", " ", norm)
    if norm in _TRIVIAL_TOKENS:
        return False
    # A lone non-zero number is fine, but a result that's only punctuation/whitespace is not.
    if not re.search(r"[a-z0-9]", norm):
        return False
    return True


# ── draft of a minimal implementation + self-test (judge) ───────────────────────

_VERIFY_DRAFT_SYSTEM = (
    "You are Chad's capability verifier. You are given a PROPOSED new capability "
    "(a tool or skill). Your job is NOT to praise it — it is to write a MINIMAL, "
    "self-contained Python script that actually IMPLEMENTS a bare-bones version of the "
    "proposed capability and then SELF-TESTS it on one concrete representative input.\n"
    "Rules:\n"
    " - The script must be fully self-contained and runnable with the Python standard "
    "library only (no pip installs, no network, no API keys). If the real capability "
    "would need the network/an API, STUB that part with realistic local fake data so the "
    "logic still executes end-to-end.\n"
    " - The script MUST, as its final action, compute the capability's output for the test "
    f"input and print exactly one line: `print('{RESULT_MARKER}' + str(output))`.\n"
    " - If a genuine minimal implementation is impossible, still print the marker with the "
    "best real value you can compute — never fabricate a fake-looking success string.\n"
    "Respond with ONLY a JSON object: {\"code\": \"<the full python script as a string>\"}. "
    "No prose, no markdown fences."
)


# A verifier draft fn: given a candidate dict, return python source (or None).
VerifyDraftFn = Callable[[dict], Awaitable[Optional[str]]]
# A sandbox runner: given python source, return the run dict (or None if sandbox is down).
SandboxRunFn = Callable[[str], Awaitable[Optional[dict]]]


def _build_judge_verify_draft_fn(model: str) -> VerifyDraftFn:
    async def _draft(cand: dict) -> Optional[str]:
        try:
            from evals.judges import build_judge_adapter, _extract_json_object
        except Exception as e:
            logger.warning(f"[ CAP_VERIFY ] judge adapter unavailable: {e}")
            return None
        adapter = build_judge_adapter(model)
        user = (
            f"PROPOSED {cand.get('kind', 'tool')}: {cand.get('title', '')}\n"
            f"DESCRIPTION: {cand.get('description', '')}\n"
            f"RATIONALE: {cand.get('rationale', '')}"
        )
        messages = [
            {"role": "system", "content": _VERIFY_DRAFT_SYSTEM},
            {"role": "user", "content": user},
        ]
        resp = await adapter.generate(
            messages=messages, temperature=0.0, thinking_level="low", max_tokens=2000
        )
        data = _extract_json_object(resp.text or "")
        if not isinstance(data, dict):
            return None
        code = data.get("code")
        return code if isinstance(code, str) and code.strip() else None

    return _draft


async def _default_sandbox_run(code: str) -> Optional[dict]:
    """Run code in the E2B sandbox. Returns the tool result dict, or None if the sandbox
    itself is unavailable (so the caller can treat it as 'unverified', not 'failed')."""
    try:
        from src.tools.coding.sandbox import ExecutePythonTool
    except Exception as e:
        logger.warning(f"[ CAP_VERIFY ] sandbox tool import failed: {e}")
        return None
    try:
        tool = ExecutePythonTool()
        result = await tool.execute(code=code)
    except Exception as e:
        # E2B has been flaky/dead before — degrade to 'unverified', never raise.
        logger.warning(f"[ CAP_VERIFY ] sandbox execution unavailable: {e}")
        return None
    if not isinstance(result, dict):
        return None
    # A clean transport failure (e.g. missing E2B_API_KEY) surfaces as success=False with an
    # 'error' that is about the sandbox, not the drafted code. Treat those as unavailable.
    if not result.get("success"):
        err = str(result.get("error") or "")
        if any(s in err for s in ("E2B_API_KEY", "Sandbox", "sandbox", "timeout", "connection")):
            logger.warning(f"[ CAP_VERIFY ] sandbox reported infra error: {err[:120]}")
            return None
    return result


# ── the verifier ─────────────────────────────────────────────────────────────


@dataclass
class CapabilityVerifier:
    """Objectively verifies a drafted capability by drafting + sandbox-running a minimal
    implementation and applying the non-trivial-result heuristic.

    `verify(cand)` returns:
      * True   — ran cleanly and produced a non-trivial result,
      * False  — ran cleanly but produced a trivial/empty/error result (real negative),
      * None   — could not verify (sandbox down, draft failed) → 'unverified'.

    The public callable returned by `as_verify_fn()` collapses None→fail_open default so it
    plugs straight into the analyzers' `verify_fn` contract (truthy = keep candidate).
    """

    draft_fn: VerifyDraftFn
    sandbox_run_fn: SandboxRunFn
    fail_open: bool = True  # when unverifiable, default to keeping the candidate (today's behavior)

    async def verify(self, cand: dict) -> Optional[bool]:
        try:
            code = await self.draft_fn(cand)
        except Exception as e:
            logger.warning(f"[ CAP_VERIFY ] draft failed for {cand.get('title')!r}: {e}")
            return None
        if not code:
            return None

        run = await self.sandbox_run_fn(code)
        if run is None:
            return None  # sandbox unavailable → unverified

        result_value = extract_result(run.get("stdout") or "")
        verdict = is_nontrivial_result(
            success=bool(run.get("success")),
            error=run.get("error"),
            stderr=run.get("stderr"),
            result_value=result_value,
        )
        logger.info(
            f"[ CAP_VERIFY ] {cand.get('title')!r}: "
            f"{'VERIFIED' if verdict else 'rejected (trivial/error)'} "
            f"(result={result_value!r})"
        )
        return verdict

    def as_verify_fn(self) -> Callable[[dict], Awaitable[bool]]:
        async def _verify_fn(cand: dict) -> bool:
            try:
                verdict = await self.verify(cand)
            except Exception as e:
                logger.error(f"[ CAP_VERIFY ] verify crashed: {e}")
                return self.fail_open
            if verdict is None:
                return self.fail_open  # unverified → don't penalize when sandbox is down
            return verdict

        return _verify_fn


# ── flag-gated builder (default OFF) ─────────────────────────────────────────────


def capability_verify_enabled() -> bool:
    """True only when CHAD_CAPABILITY_VERIFY is explicitly enabled. Default OFF — nothing
    in the self-improvement pipeline changes silently."""
    return (os.getenv("CHAD_CAPABILITY_VERIFY", "") or "").strip().lower() in {"1", "true", "yes", "on"}


def build_capability_verify_fn() -> Optional[Callable[[dict], Awaitable[bool]]]:
    """Return a ready-to-inject `verify_fn`, or None when the feature is flagged off.

    Both analyzer builders call this and pass the result straight through to their
    `verify_fn` field — so when the flag is off they receive None and behave as today.
    """
    if not capability_verify_enabled():
        return None
    model = os.getenv("CHAD_CAPABILITY_JUDGE_MODEL", "qwen/qwen3.5-9b")
    verifier = CapabilityVerifier(
        draft_fn=_build_judge_verify_draft_fn(model),
        sandbox_run_fn=_default_sandbox_run,
    )
    logger.info("[ CAP_VERIFY ] Objective capability verification ENABLED (CHAD_CAPABILITY_VERIFY).")
    return verifier.as_verify_fn()
