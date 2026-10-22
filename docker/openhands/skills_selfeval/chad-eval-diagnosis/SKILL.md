---
name: chad-eval-diagnosis
description: When the task is to diagnose or fix a failure in Chad's OWN harness eval (issue #358 self-improvement loop). Explains what the Chad project is, where each class of behaviour lives, how to read an eval report + transcript (especially the model's reasoning), and how to tell a real model failure apart from a harness/infra failure.
---

# Diagnosing Chad's own eval failures

You are being pointed at **Chad**, a Windows AI assistant, mounted read-only at `/repo` (explore
it with the `codegraph_*` tools — see the large-repo-explore skill). The task is a
**self-improvement** one: a harness eval that exercises Chad's own behaviour is failing (or a
reviewer wants a judgement about Chad's behaviour), and your job is to find the root cause in
Chad's source and either **propose** a fix or **make** one — the same shape as a contributor's
fork → PR.

This is not ordinary greenfield coding. Read this before you start.

## First, the hard rule

**Never edit the eval to make it pass.** The eval (`evals/`, its datasets under
`evals/datasets/*.jsonl`, its assertions, its baselines) is the *specification*. Changing it is a
failed run, not a fix. Fix Chad's **source** so the real behaviour meets the spec. If you become
convinced the eval itself is genuinely wrong, **say so explicitly and stop** — do not silently
edit it.

## What Chad is (the map you need)

Chad is a headless Python **Brain** (a server) plus a desktop **Limb** (GUI). You almost never
touch the GUI. Behaviour that evals grade lives in a few places — go straight to them:

- **Tool & subagent selection** — which tool/agent Chad picks for a prompt:
  - `src/infrastructure/tools/selection/` (the LLM-driven selector + RAG shortlist)
  - `src/infrastructure/tools/delegate_routing.py` (routing to specialist sub-agents)
  - `src/infrastructure/tools/registry.py` (what tools exist / are discoverable)
- **Orchestration & routing** — how a turn is run, planned, and advanced:
  - `src/core/orchestration/` — especially `nodes/` and `turn/`
  - `src/core/orchestration/conversation_orchestrator.py` (turn entry point)
- **Prompts / persona** — `prompts/persona/<id>.md`. These are **plain files and ARE the live
  prompts** — e.g. `agent.md` is the main supervisor, `code_agent.md` / `traversal_agent.md`
  / `browser_agent.md` are specialists. A "wrong reasoning / wrong tool" failure is very often a
  prompt problem, not a code problem. Editing the right `.md` here is a legitimate, minimal fix.
- **The eval harness itself** — `evals/AGENTS.md` / `evals/REFERENCE.md` explain the lanes; `evals/core.py` defines the
  assertions. Read these to understand *what was measured*, but do not edit them to pass.

Use `codegraph_explore("how does tool selection work")` (etc.) to pull the real source of any of
these in one call rather than guessing.

## You CANNOT run the eval yourself — and that's expected

This sandbox has **none of Chad's Python dependencies**. `python -m evals.runner ...` will only
ever raise `ModuleNotFoundError` (e.g. `No module named 'langchain_core'`). **Do not spend turns
trying to run it.** The failure output is already captured for you and pasted into your brief, and
**verification runs automatically after you finish**, on the host, via a critic. Make your change
and finish; the critic re-runs the real eval and tells you pass/fail.

Your tools here are: **codegraph** (explore `/repo`), **Context7** (`query-docs`, current library
docs), **web-search** (Brave, via the skill), and the **terminal** (for non-code files). That's
it — no Gmail, Drive, or Chad-internal APIs.

## How to read what you were handed

Your brief contains two things. Read both.

1. **The report summary** — tells you WHICH case failed and WHY at the assertion level:
   - `- ASSERT: <...>` — a deterministic assertion tripped (e.g. `min_steps`, `max_duration_s`,
     `tools_include`, `response_includes`). This is the literal contract that was broken.
   - `- JUDGE[<rubric>] score=... : <reasons>` — an LLM judge's verdict and its reasoning.
   - `- metrics: ...` — tool count, tokens, plan steps, duration.
2. **The full failing-case trajectory** (under "Full failing-case trajectories") — the real
   signal. It contains:
   - `## Reasoning (head+tail)` or `## Reasoning by agent` — **the model's actual thinking.**
     This is usually where the failure really is: it picked the wrong tool because it
     mis-framed the goal, or it gave up, or it looped. Read this first.
   - `## Tools executed` — every tool call with its params (so you can see a wrong ref, a wrong
     query, a missing arg).
   - `## Plan proposed` / `## Final plan state` / `## Round-by-round progress` — for planner and
     workflow failures, where the plan under-decomposed or stalled.

Diagnose from the **reasoning + tools**, not just the assertion line. "min_steps: plan has 1 step"
tells you the symptom; the reasoning tells you the model thought one step was sufficient — and
*that* is what a prompt or code fix has to change.

## Tell a real failure apart from a harness failure

Not every red eval means Chad behaved wrong. Do **not** rewrite working code in response to these:

- **`aborted_reason` / "HARNESS failure" / "did not complete"** — the eval hung or was killed.
  Infra, not your fix.
- **`[LATENCY-ONLY — tool selection/judges were clean, only the duration budget was missed]`** in
  the report — Chad did the *right* thing, just slower than a `max_duration_s` budget. This is a
  timing/budget question, usually not a correctness bug in the code you'd edit.
- **`ModuleNotFoundError` / import errors** — that's the sandbox missing deps (see above), never a
  Chad bug to fix.
- **"No changes were made"** — you didn't actually edit any source; the eval wasn't run. Make the
  edit.

If the failure is one of these, the right move is often to **say so** rather than to patch — an
honest "this is a harness/latency issue, not a code bug, here's why" is a good outcome.

## Two modes — do what the brief asks

- **Diagnose-only (suggestion):** when the brief says to *analyse* and *propose* without editing —
  produce a root-cause diagnosis citing exact files/symbols and a concrete proposed fix, and
  **stop without editing**. The value is the judgement.
- **Fix-and-verify:** when the brief says to *fix* it — make the **smallest** change that
  addresses the root cause (prefer a prompt tweak or a targeted code change over a broad
  refactor), then finish. The critic re-runs the eval to confirm. Don't gold-plate; don't touch
  unrelated files.

## Checklist before you finish

- Root cause identified in Chad's **source** (a file/symbol under `src/…` or `prompts/…`), not
  guessed.
- Change is **minimal** and matches the surrounding style.
- You did **not** edit anything under `evals/`.
- If you concluded it's a harness/latency/spec issue rather than a code bug, you said so plainly
  instead of forcing a patch.
