---
name: systematic-debugging
description: Use when encountering any bug, test failure, or unexpected behavior in this repo, before proposing a fix. Adapted from obra/superpowers.
---

# Systematic Debugging

## Overview

Random fixes waste time and create new bugs. Quick patches mask the underlying issue and come back later as a different-looking bug.

**Core principle:** find root cause before attempting a fix. Symptom fixes are a failure mode, not a shortcut.

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

## When to use

Any bug, test failure, unexpected behavior, performance regression, or build failure — especially when:
- A "quick fix" seems obvious
- You've already tried one fix and it didn't work
- You're under time pressure (guessing is slower than it feels)

## The Four Phases

### Phase 1: Root Cause Investigation
1. **Read the full error** — stack trace, line numbers, exit codes, not just the first line.
2. **Reproduce consistently.** If you can't reproduce it reliably, gather more data before guessing.
3. **Check recent changes** — `git diff`, recent commits, new deps/config.
4. **In multi-component systems** (Brain↔Limb over WebSocket, selector→planner→executor, tool call→MCP server), add diagnostic logging at each boundary before theorizing which layer is broken. Don't assume — this repo has a documented history of "harness bug" turning out to be a config/env issue three layers down (see LM Studio GPU-layers gotcha, prod Supabase key gotcha).
5. **Trace data backward** from the error to its origin if it's deep in a call stack.

### Phase 2: Pattern Analysis
- Find a working example doing something similar in this codebase.
- Diff it against the broken code line by line — don't assume small differences don't matter.

### Phase 3: Hypothesis and Testing
- State one hypothesis explicitly: "I think X is the root cause because Y."
- Make the smallest possible change to test it. One variable at a time.
- If it doesn't confirm, form a new hypothesis — don't stack a second fix on top of the first.
- If you don't understand something, say so rather than guessing.

### Phase 4: Implementation
1. Write a failing test/repro case first if practical.
2. Fix the root cause — one change, no bundled "while I'm here" cleanup (that's a separate change).
3. Verify: does the original failing case now pass? Did anything else break?
4. **If the fix doesn't work: stop and count.** After 3 failed fix attempts on the same issue, the problem is architectural, not a bad guess — surface that to the user instead of trying a 4th patch.

## Red flags — stop and return to Phase 1

- "Quick fix for now, investigate later"
- "Let me just try changing X and see"
- Proposing a fix before you've traced where the bad value actually originates
- Each attempted fix reveals a new problem in a different place (architecture smell)

## Common rationalizations to reject

| Excuse | Reality |
|---|---|
| "Issue is simple" | Simple bugs have root causes too; the process is fast for small bugs. |
| "Emergency, no time" | Systematic debugging is faster than guess-and-check thrashing. |
| "I'll write a test after confirming the fix" | Untested fixes don't stick — test first proves the fix, not your confidence. |
| "I see the problem, let me fix it" | Seeing a symptom isn't understanding root cause. |
