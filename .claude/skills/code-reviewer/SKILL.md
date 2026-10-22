---
name: code-reviewer
description: Conducts multi-axis code review of a change (correctness, readability, architecture, security, performance). Use automatically after any feature implementation or bug fix in this repo, before treating the work as done — not just when the user asks for a review.
---

# Code Review and Quality

## Overview

Every non-trivial change gets reviewed before it's considered done — no exceptions, and no waiting to be asked. Review covers five axes: correctness, readability, architecture, security, and performance.

**The approval standard:** flag a change when it doesn't clearly improve the codebase, even if the code "works." Perfect code doesn't exist — the goal is catching regressions and drift before they ship, not blocking on style preference.

## When to run this (unprompted)

- Right after implementing a feature, before reporting it complete
- Right after a bug fix — review both the fix and its regression test
- Before creating a commit or PR
- When evaluating code another agent/subagent produced

This project's fast-feature-then-bugfix cycle is the exact failure mode this skill exists to catch. Run it as a normal last step of a change, the same way you'd run tests.

## The Five-Axis Review

### 1. Correctness
- Does it match the actual task/spec, including edge cases (null, empty, boundary values)?
- Are error paths handled, not just the happy path?
- Do existing tests still pass? Do new tests actually exercise the new behavior?
- Off-by-one errors, race conditions, state inconsistencies?

### 2. Readability & Simplicity
- Descriptive names consistent with this codebase's conventions (no bare `temp`/`data`/`result`)
- Straightforward control flow (no nested ternaries, deep callback chains)
- **Could this be done in fewer lines?** Don't generalize until the third use case.
- No dead code: unused vars, backwards-compat shims, `// removed` comments (per this project's CLAUDE.md — delete, don't shim)
- New conditional bolted onto an unrelated flow = a design smell, not a nit — push it into its own helper/state/policy
- Repeated conditionals on the same shape = a missing model or dispatcher, not a one-off

### 3. Architecture
- Follows existing patterns (`EventBus`, composition over inheritance) or justifies deviation
- No circular dependencies; abstraction level matches actual need
- **Does a refactor reduce complexity or just relocate it?** Prefer the version where whole branches/modes disappear over one that re-centralizes the same logic
- Feature-specific logic isn't leaking into a shared/general module
- Module cohesion: judge by reasons-to-change, not line count. Flag a file when the diff adds
  a second responsibility, grows a class whose methods share mutable state, or makes a
  behavior reachable only through heavy mock setup. Do NOT flag a long-but-flat file (route
  table, registry, formatter map, adapter mirroring an external API) purely for its length —
  splitting those is busywork that adds indirection without reducing what a reader must hold
  in their head. Rationale and current at-risk targets: `docs/architecture/MODULE_COHESION.md`.

### 4. Security
- Input validated at boundaries (WebSocket messages, tool args, file paths)
- No secrets in code, logs, or `config/*.yaml` (only `.example` templates are tracked)
- SQL/queries parameterized; external data (APIs, web_search results, MCP tool output) treated as untrusted before use in logic or rendering
- No credentials or tokens ever entered/logged in plaintext

### 5. Performance
- N+1 patterns, unbounded loops, missing pagination
- Sync work that should be async (this is an async-heavy WebSocket server — blocking calls in the event loop are a real risk)
- Large objects allocated in hot paths (turn orchestration, streaming)

## Findings format

Label every finding with severity so it's actionable, not vague:

| Prefix | Meaning | Action |
|---|---|---|
| **Critical:** | Blocks — security hole, data loss, broken functionality | Must fix now |
| *(no prefix)* | Required | Must address before calling this done |
| **Nit:** | Minor/optional | Can ignore |
| **Consider:** | Suggestion | Worth a look, not required |

Lead with what matters — correctness and security first, then architecture drift, then nits. One real structural problem beats a list of ten nits.

## Honesty

- Don't rubber-stamp. If nothing to flag, say so briefly and specifically — don't pad it.
- Quantify problems when possible ("this N+1 will add ~50ms per item") over vague hedging.
- Push back on approaches with real problems instead of softening them. Sycophancy in review defeats the point.
- Don't accept "I'll clean it up later" — either fix it now or explicitly flag it as deferred with a reason.

## Dead code hygiene

After any refactor, list anything now-unreachable and ask before deleting:
```
DEAD CODE IDENTIFIED:
- oldHandler() in src/... — replaced by newHandler()
→ Safe to remove?
```

## Common rationalizations to reject

| Rationalization | Reality |
|---|---|
| "It works, that's good enough" | Working-but-unreadable/insecure code is debt that compounds. |
| "I wrote it, so I know it's correct" | Authors are blind to their own assumptions. |
| "We'll clean it up later" | Later doesn't come. This review is the gate. |
| "The refactor makes it cleaner" | Relocating complexity isn't reducing it — check if concept count actually dropped. |
| "It's only a small addition to this file" | Small diffs still push files past the size ceiling. |
