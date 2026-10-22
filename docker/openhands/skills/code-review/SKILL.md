---
name: code-review
description: Self-review your own changes before finishing — catch bugs, missing edge cases, security issues, and sloppy code while you still have iterations left.
---

# Self Code Review

You have **no external reviewer** and no built-in success signal. Before you call the task
done, review your own work as if a senior engineer were about to critique it. This is the single
highest-leverage habit for a local model — most failures are things you'd have caught on a reread.

## Do this before finishing every coding task

1. **Re-read the actual diff / the files you wrote.** Not your memory of them — open them.
2. **Trace the main path.** Walk one real input through the code end to end. Does it produce the
   right output? Off-by-one, wrong variable, swapped args?
3. **Check the edges:** empty input, zero, negative, missing key, null/None, large input, the
   error path. Local models routinely handle the happy path and drop the edges.
4. **Security & safety:** no hardcoded secrets, no unsanitized shell/SQL/HTML from input, no
   path traversal, no `eval` on untrusted data.
5. **Run the verification the task gave you** (tests, a command, "it prints X"). If it fails,
   you are NOT done — fix and re-check. If there are tests, run them; don't assume.
6. **Clean up:** remove debug prints, dead code, and TODOs you actually resolved. Match the
   surrounding style.

## Output
End with a one-line self-assessment: what you built, that you re-read it, and the concrete
evidence it works (test output / command result). If you couldn't verify something, say so
explicitly rather than claiming success.

## Anti-patterns
- ❌ Declaring done without re-reading the files or running the check.
- ❌ "The tests should pass" — run them.
- ❌ Fixing the symptom the example showed and ignoring the general case.
