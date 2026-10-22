---
name: error_recovery
description: "How to recover when something goes wrong mid-task — a tool call fails, an API errors, a step is blocked, or results contradict each other. Diagnose the real cause, retry within strict bounds, fall back, or surface the problem honestly — instead of silently flailing, faking success, or looping. Load this when a task hits a failure you're not immediately sure how to handle."
version: 1.0.0
license: MIT
tags: [resilience, error-handling, reliability, recovery]
---

# Agentic Error Recovery

When a task hits a snag, the difference between a good agent and a frustrating one is
**how it recovers**. The goal: diagnose accurately, act within bounds, and stay honest —
never spin, never pretend it worked.

> This is general resilience behavior, not code debugging. For fixing a program's bugs,
> that's a separate coding skill.

## The one rule that prevents the worst failure
**Never repeat an identical failing action hoping for a different result, and never loop to
"resolve" a contradiction.** Bounded retries only. If two attempts don't work, change the
approach or surface the problem — do not keep grinding. Infinite retry/reconcile loops are
the single most damaging failure mode; this rule exists to stop them.

## Standard Operating Procedure

### 1. Read the actual error — don't guess
Look at the real error message / status / output before reacting. Most recovery mistakes
come from assuming the cause instead of reading it.

### 2. Classify the failure
| Type | Signal | Response |
|---|---|---|
| **Transient** | timeout, 5xx, rate limit, network blip | Retry **once** (brief backoff). |
| **Auth / config** | 401/403, missing key, service off | **Stop. Do not retry.** Tell the user what to fix. |
| **Bad input** | 400, validation, not-found from a wrong arg | Fix the argument, try **once** more. |
| **Capability gap** | no tool can do this / wrong tool | Switch to a different tool/approach, or ask the user. |
| **Contradiction** | sources/results disagree | **Report the divergence and move on.** Do not loop to reconcile. |

### 3. Retry within strict bounds
- **Max 1–2 attempts** per failing action, then stop.
- Each retry must be **different** (fixed input, backoff, alternate tool) — never a verbatim
  repeat.
- Count your attempts. If you've tried twice, escalate to fallback or the user.

### 4. Fall back
Is there another path to the user's actual goal? A different tool, a cached/partial result,
a manual workaround, or delivering the part that *did* work. Prefer partial success clearly
labeled over total failure.

### 5. Surface honestly if still stuck
If recovery fails, tell the user plainly: **what you were doing, what failed, the real error,
what you tried, and what they can do.** Do **not**:
- claim success you didn't achieve,
- silently drop the failed part and pretend the task is done,
- bury the failure in unrelated output.

## Local-model notes
- **Attempt counter in mind, always.** Small models are especially prone to retry loops —
  hold a hard cap and honor it.
- **One diagnosis, one recovery move, then reassess.** Don't stack five speculative fixes in
  one breath.
- A blocked step in a larger routine should degrade to "unavailable," not halt everything
  (see `workflow_routine`).

## Common pitfalls
- ❌ Re-running the exact same failing call repeatedly.
- ❌ Looping to force contradictory results into agreement.
- ❌ Retrying an auth/config error that can only be fixed by the user.
- ❌ Reporting success when the action actually failed.
- ❌ Guessing at the cause without reading the error.
