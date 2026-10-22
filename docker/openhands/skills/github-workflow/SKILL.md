---
name: github-workflow
description: How to work with git and GitHub inside a coding job — branches, commits, and (when a GitHub MCP is connected) issues and PRs. Use whenever the task touches version control.
---

# Git / GitHub Workflow

You work in `/workspace` (writable) via the terminal. Git may or may not be initialized; a
GitHub MCP server may or may not be connected. Adapt to what's actually present — check, don't
assume.

## Git basics (always available via the terminal)
1. **Check state first:** `git status` and `git log --oneline -5` before doing anything.
2. **Never commit on top of an unrelated dirty tree.** If the workspace is a real repo, make a
   branch: `git checkout -b <short-descriptive-name>`.
3. **Commit in small, coherent steps** with clear messages (imperative mood: "Add X", "Fix Y").
   One logical change per commit. Don't bundle unrelated edits.
4. **Only commit when the change is complete and verified** (see the code-review skill). Don't
   commit broken or half-finished code unless explicitly asked to checkpoint.
5. **Do not force-push, hard-reset, or delete branches** unless the task explicitly says so.

## GitHub MCP (only if a GitHub tool is present in your toolset)
If you see GitHub tools available (issue/PR/repo tools), you can:
- Read issues/PRs for context before coding.
- Open a PR when the task asks you to — with a title and a body that explains *what* and *why*,
  and how it was verified.
- Comment on issues/PRs to report progress or results.

**Guardrails:**
- Never open/merge a PR, close an issue, or push to a shared branch unless the task explicitly
  asks for it. When in doubt, do the local git work and describe the PR you *would* open rather
  than opening it.
- Never expose tokens or secrets in commits, PR bodies, or comments.
- If no GitHub tools are present, stick to local git and report what you did in your final
  message — don't pretend you opened a PR.

## Reporting
End with the branch name, the commits you made (short SHAs + messages), and — if you used the
GitHub MCP — the URL of anything you created.
