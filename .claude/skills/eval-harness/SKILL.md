---
name: eval-harness
description: Use when the user asks to test the harness, run evals, add/write an eval case, check tool or subagent selection, find prompt contradictions/bloat, or verify a change to prompts/tool-selection/orchestration nodes actually works. Covers both running the existing evals/ suite and writing new cases/probes.
---

# Chad Eval Harness

`evals/` tests the **harness** (pipeline configure/route), not only answer quality.

**Read these — do not reinvent the runbook here:**

1. [`evals/AGENTS.md`](../../../evals/AGENTS.md) — **SSOT**: Matt defaults, worktree Brain `:8788`,
   bootstrap, lanes, ports, common mistakes
2. [`evals/HARNESS_LESSONS.md`](../../../evals/HARNESS_LESSONS.md) — failure classes / wins (append)
3. [`docs/evals/FOUND_AND_FIXED.md`](../../../docs/evals/FOUND_AND_FIXED.md) — known bugs already fixed
4. [`evals/REFERENCE.md`](../../../evals/REFERENCE.md) — schema, rubrics, probes, CI
5. [`evals/README.md`](../../../evals/README.md) — short index

Judge = analysis/classify. Fix + retest = Chad-on-Chad
([`CHAD_SELF_EVAL_LOOP.md`](../../../docs/architecture/CHAD_SELF_EVAL_LOOP.md), issue #358).

HITL quality design: [`docs/evals/QUALITY_HITL.md`](../../../docs/evals/QUALITY_HITL.md).
