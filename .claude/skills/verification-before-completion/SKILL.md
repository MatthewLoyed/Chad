---
name: verification-before-completion
description: Use before claiming any work is complete, fixed, or passing, and before committing or creating a PR. Requires running the actual verification command and reading its output before making a success claim. Adapted from obra/superpowers.
---

# Verification Before Completion

## Overview

Claiming work is done without checking is a false report, not efficiency. This directly targets this project's recurring pattern of shipping a feature fast and finding the bugs later — the fix is verifying before declaring victory, not after.

**Core principle:** evidence before claims, always.

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the check in this turn, you cannot claim it passes.

## The gate

Before saying something works, passes, or is fixed:
1. Identify what command/action actually proves the claim.
2. Run it fresh, in full (not a memory of a prior run).
3. Read the full output — exit code, failure count, not just the last line.
4. Only then state the claim, with the evidence behind it.

## What each claim actually requires

| Claim | Requires | Not sufficient |
|---|---|---|
| "Tests pass" | Test command output, 0 failures | Prior run, "should pass" |
| "Build succeeds" | Build/compile command, exit 0 | Linter passing |
| "Bug fixed" | Re-run the original failing repro, now passes | Code changed, assumed fixed |
| "Regression test works" | Red (before fix) → green (after fix) both observed | Passes once |
| "Subagent completed the task" | Diff/output actually inspected | Subagent's own "success" report |
| GUI/frontend change works | Actually opened in browser/app and exercised the golden path | Type check + unit tests only |

## Red flags — stop before claiming success

- Using "should", "probably", "looks right"
- Saying "Done!"/"Fixed!" before the command has been run this turn
- About to commit/push/open a PR without having run the relevant check
- Trusting a subagent's self-reported "success" without checking its diff
- "Just this once" / "I'm confident" — confidence isn't evidence

## Why this matters here

Your CLAUDE.md instructions already say type-checking and test suites verify correctness, not feature correctness, and that UI changes need an actual browser check. This skill is the enforcement mechanism for that — treat it as the last step of every change, not optional polish.
