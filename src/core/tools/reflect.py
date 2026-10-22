"""
reflect.py — force an explicit diagnosis before a repair attempt.

THE EVIDENCE
------------
Structured reflection is the single highest-yield intervention measured for
models in our size class. Feeding back `failed call + error` and requiring the
model to *state the diagnosis* before proposing a corrected call lifted repair
rates substantially at small scale (Qwen3-4B 9.6% -> 14.9% on the first attempt;
Llama-3.1-8B 4.7% -> 20.5% -> 26.4% across 1/3/5 attempts). The gain comes from
making the diagnosis EXPLICIT, not from the extra tokens: a blind retry and a
reasoned repair look identical in the trace until you force the sentence.

Related work also finds models catch errors in external content far more
reliably than in their own reasoning — so self-correction has to be triggered by
the harness. Waiting for the model to notice it is looping does not work.

WHY A SEPARATE PROMPT AND NOT MORE PERSONA
------------------------------------------
`_build_step_recovery_feedback` (supervisor_node) already writes a diagnosis FOR
the model. That helps, but it is the harness reasoning, not the model — and the
measured gain is specifically from the model producing it. This asks for one
sentence, at the moment it matters, which is roughly 20 tokens of context.

WHEN IT FIRES
-------------
Only on the SECOND consecutive failure of the same tool. The first failure
already carries `NEXT_ACTIONS` from the failure envelope, which is usually
enough; a model that follows it does not need this. The second failure is the
signal that it did not, and is about to blind-retry again.
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.core.tools import failure


def _tool_failure_streak(messages: List[Any]) -> tuple[Optional[str], int]:
    """(tool_name, consecutive failure count) for the most recent tool.

    Walks back over ToolMessages only, stopping at the first success — so a run
    of failures broken by anything that worked resets the count. That matters:
    partial progress means the model is not stuck, it is working.
    """
    from langchain_core.messages import ToolMessage

    streak_tool: Optional[str] = None
    count = 0
    for msg in reversed(messages or []):
        if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
            continue
        sig = failure.failure_signature(msg.content)
        if sig is None:
            break  # a success ends the streak
        _, _, tool = sig.partition(":")
        tool = tool or "the tool"
        if streak_tool is None:
            streak_tool = tool
        elif tool != streak_tool:
            break
        count += 1
    return streak_tool, count


REFLECT_HEADER = "### 🔁 DIAGNOSE BEFORE YOU RETRY"


def build_reflection_directive(messages: List[Any]) -> Optional[str]:
    """Turn-context block asking for an explicit diagnosis, or None.

    Returns None unless the same tool has failed at least twice in a row — the
    point at which the model has demonstrably not acted on the NEXT_ACTIONS it
    was already given.
    """
    tool, streak = _tool_failure_streak(messages)
    if not tool or streak < 2:
        return None

    return (
        f"\n\n{REFLECT_HEADER}\n"
        f"`{tool}` has now failed {streak} times in a row. Before your next call, "
        f"write ONE short sentence in this exact shape:\n"
        f"DIAGNOSIS: <what specifically went wrong> -> CHANGING: <the one thing "
        f"you are doing differently>\n"
        f"Then make the call. Rules:\n"
        f"- 'CHANGING: trying again' is not a change. Name a different argument, "
        f"a different tool, or a different route.\n"
        f"- If you cannot name a real change, stop retrying: report what you have "
        f"and what blocked you.\n"
    )
