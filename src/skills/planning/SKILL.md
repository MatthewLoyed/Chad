---
name: planning
description: "Break a fuzzy, multi-step goal into an ordered, checkable plan before acting. Use when the user gives a big or vague objective ('help me plan X', 'how should I approach Y', 'organize this project', 'I need to get Z done'), when a task has several dependent steps, or when you're about to do something involved and want a plan first. This is the backbone other skills lean on."
version: 1.0.0
license: MIT
tags: [planning, task-breakdown, strategy, workflow]
---

# Planning

Turn a vague goal into a **concrete, ordered, checkable plan** — so both you and the user
can see the path, spot what's missing, and track progress. Plan first, act second.

## When to use

- The goal is big, vague, or open-ended ("plan my move", "get the app launched").
- The task has **multiple dependent steps** where order matters.
- You're about to start something involved and want alignment before spending effort.
- **Skip it** for single-step requests — just do them. Don't plan "send this email."

## Standard Operating Procedure

### 1. Clarify the goal (one question max)
Restate the objective in one sentence. If a *decision-changing* detail is missing
(deadline, budget, scope, definition of done), ask **one** question. Otherwise state your
assumption and proceed — don't stall the plan behind a questionnaire.

### 2. Decompose
List the concrete steps needed to reach the goal. Each step must be:
- **Actionable** — starts with a verb, is something you or the user can actually do.
- **Verifiable** — has a clear "done" condition, not "work on X".
- **Right-sized** — if a step hides three sub-tasks, split it.

### 3. Order by dependency
Sequence steps so each one's prerequisites come first. Mark steps that **can run in
parallel** and steps that are **blocked by** an earlier one. Call out the critical path.

### 4. Surface unknowns & risks
Explicitly flag: what's uncertain, what could block progress, what needs a decision from
the user. A named unknown is worth more than a confident-but-wrong step.

### 5. Present, then (optionally) track
Show the plan. Offer to persist it if it's ongoing — e.g. save tasks to `todoist`, or turn
a recurring plan into a routine (`workflow_routine`) or a scheduled task.

## Output format

```
**Goal:** <one sentence>  ·  **Done when:** <definition of done>

1. [ ] <step> — <done condition>
2. [ ] <step>  (depends on 1)
3. [ ] <step>  (parallel with 2)
...

**Open questions / risks:** <what needs a decision or could block>
```

## Local-model notes
- **Don't over-plan.** 3–8 steps for most tasks. A 25-step plan for a small job is noise.
- **Keep it linear and concrete.** Small models execute a flat checklist far more reliably
  than a nested tree of conditional branches.
- **Plan, then hand off.** Once the plan is set, execute it (or delegate). Do not re-plan
  after every completed step — update the checklist and keep moving.

## Common pitfalls
- ❌ Vague steps ("handle the backend") with no done condition.
- ❌ Planning forever instead of starting — the plan is a means, not the deliverable.
- ❌ Ignoring dependencies so step 4 blocks on step 6.
- ❌ Re-writing the whole plan mid-execution instead of ticking boxes.
