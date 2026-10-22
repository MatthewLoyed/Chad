"""ChadEvalCritic — make a real Chad eval the coder's success signal (issue #358).

DEV-ONLY — must never ship in a public release. See evals/SELF_EVAL_DO_NOT_SHIP.md.


Chad Code has no built-in notion of "done": every tier it has passed so far passed because
the brief handed it a verification command it could run itself (a test suite, a runnable CLI).
The eval-fix-eval loop needs the same thing, but the eval CANNOT run inside the container:

  * A live eval drives a real Chad turn, which needs the MAIN model resident.
  * The coder needs the 35B resident.
  * The VRAM budget is 16 GB. They do not coexist.

So the eval runs on the HOST, and the container asks for it over a narrow HTTP bridge
(``evals/chad_code_bridge.py``). The bridge owns the model swap: unload coder -> load main ->
run the eval -> unload main -> reload coder -> return the verdict.

Why a *critic* rather than a tool the agent calls:

  1. The agent cannot skip verification. A tool is a choice; a critic is structural — the SDK
     runs it after the agent finishes, every time.
  2. ``IterativeRefinementConfig`` makes the retry budget declarative. ``Conversation.run()``
     re-prompts the agent with the failure and retries until the score clears the threshold or
     ``max_iterations`` is hit. That is #358's "max 3 attempts" requirement, for free.
  3. The swap happens BETWEEN agent steps, when no inference is in flight. Evicting the coder
     mid-step would fail its next call or trigger a JIT reload that evicts the Brain; after a
     step there is nothing running, and the container holds its conversation state in Python
     memory, not in VRAM. So the coder is simply frozen while the host does its thing.

The critic never edits anything and never runs an arbitrary command: it names a suite the
bridge has allowlisted. A hung eval comes back as a structured abort, not silence — see
``score=0.0`` with ``aborted`` metadata below.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from openhands.sdk.critic import CriticBase, CriticResult

# NOTE (verified against SDK 1.17.0): openhands/sdk/agent/critic_mixin.py calls
#   self.critic.evaluate(events=..., git_patch=None)
# with a literal "# Evaluate without git_patch for now". So git_patch is ALWAYS None here,
# no matter what the agent did. An earlier version of this critic short-circuited on an empty
# patch — which would have scored every single run 0.0 with "no changes were made" and never
# run one eval. Do NOT reintroduce a git_patch-based check in this class.
#
# The "did anything actually change?" question is answered on the HOST instead, by the bridge,
# which can inspect the real workspace with git. That is strictly better information anyway:
# it sees the working tree rather than the agent's belief about it.


class ChadEvalCritic(CriticBase):
    """Scores a run by running a real Chad eval suite on the host.

    score is deliberately binary (1.0 pass / 0.0 fail) rather than a likelihood: an eval suite
    is a hard gate, and a partial score would let a still-failing run clear the threshold.
    """

    bridge_url: str = "http://host.docker.internal:8799"
    suite: str = "smoke"
    case_id: str | None = None
    # HOST path of the workspace this container is editing (the container sees it at /workspace;
    # the bridge runs the eval there). Without it the eval would run against the host checkout and
    # measure code the coder never touched — reporting "no improvement" forever.
    workspace: str | None = None
    # Wall-clock cap for the WHOLE bridge round-trip (two model swaps + the suite), not just
    # the suite. Generous by default: a 20 GB swap is ~60-120s each way.
    timeout_s: int = 1800

    def evaluate(
        self,
        events: Sequence[Any],  # noqa: ARG002 — verdict comes from the eval, not the transcript
        git_patch: str | None = None,
    ) -> CriticResult:
        try:
            payload = self._call_bridge()
        except Exception as exc:  # noqa: BLE001 — an unreachable bridge must not crash the job
            # Score 0 so the agent gets another attempt, but say plainly that the harness
            # failed rather than the fix — otherwise the agent "fixes" a phantom bug.
            return CriticResult(
                score=0.0,
                message=(
                    f"Could not reach the eval bridge at {self.bridge_url} "
                    f"({type(exc).__name__}: {exc}). This is a HARNESS failure, not a problem "
                    "with your change. Do not rewrite working code in response to this."
                ),
                metadata={"bridge_error": f"{type(exc).__name__}: {exc}"},
            )

        passed = bool(payload.get("passed"))
        aborted = payload.get("aborted_reason")
        output = (payload.get("output") or "").strip()

        if payload.get("no_changes"):
            return CriticResult(
                score=0.0,
                message=payload.get("message") or "No changes were made to the workspace.",
                metadata={"no_changes": True, "eval_skipped": True},
            )

        if aborted:
            # A hung/killed eval is NOT evidence the fix is wrong. Same reasoning as above.
            return CriticResult(
                score=0.0,
                message=(
                    f"The eval did not complete: {aborted}. This is a HARNESS failure, not a "
                    "verdict on your change. Do not rewrite working code in response to this."
                ),
                metadata={"aborted": aborted, "suite": self.suite, "case_id": self.case_id},
            )

        return CriticResult(
            score=1.0 if passed else 0.0,
            message=(
                f"Eval suite '{self.suite}' PASSED." if passed
                else f"Eval suite '{self.suite}' still FAILS.\n\n{output}"
            ),
            metadata={
                "suite": self.suite,
                "case_id": self.case_id,
                "passed": passed,
                "duration_s": payload.get("duration_s"),
            },
        )

    def get_followup_prompt(self, critic_result: CriticResult, iteration: int) -> str:
        """What the agent sees when the eval failed and it gets another attempt.

        The base implementation reports a "predicted success likelihood", which is meaningless
        for a hard gate and invites the agent to argue with the score. Replace it with the
        actual failure output and a warning against the one shortcut that would break trust.
        """
        return (
            f"Attempt {iteration} did not pass verification.\n\n"
            f"{critic_result.message}\n\n"
            "Diagnose why it still fails and make a MINIMAL, targeted fix to the source. "
            "Do NOT edit the eval, its dataset, or its assertions to make it pass — the eval "
            "is the specification, and changing it is a failed run, not a fix. If you believe "
            "the eval itself is wrong, say so explicitly and stop instead of editing it."
        )

    def _call_bridge(self) -> dict:
        body = json.dumps({
            "suite": self.suite,
            "case_id": self.case_id,
            "timeout_s": self.timeout_s,
            "workspace": self.workspace,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.bridge_url.rstrip('/')}/eval",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
