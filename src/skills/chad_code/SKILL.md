---
name: chad_code
description: "Delegate real coding work to Chad Code — a background OpenHands worker running a local model 24/7. Use when the user wants software actually built, changed, or explored across files (a multi-file project, a script, edits to a codebase, 'explore repo X and report'), not a quick inline snippet. Covers the gather-context-first workflow, how to write the hand-off brief, the toggles, and how to check/stop a job."
version: 1.0.0
tags: [coding, openhands, delegation, background-job, chad-code]
metadata:
  category: dev
---

# Chad Code

**Chad Code** is a *background coding worker* you dispatch to. It's OpenHands driving a local
model in a Docker sandbox, so it can grind on real coding work with no cloud usage limits. It is
**not** in your turn loop: you seed a job, hand off, and report results back later. You never
block a turn waiting for it.

You (Chad) are the **model-manager + task-framer + reporter**. OpenHands is the hands. Your job
is to turn a vague ask + whatever context you can gather into a crisp, self-contained brief, then
dispatch and follow up.

## When to use this vs. other paths

- **`dispatch_coding_task` (Chad Code)** — real software work: build a multi-file project, edit an
  existing codebase, write+run scripts, explore a repo and report. Heavy, background, tireless.
- **`delegate_to_code_agent`** — quick, *in-turn* artifacts in the cloud sandbox: a one-off Python
  snippet, a chart, a single HTML page. Fast and synchronous. Use when the user wants the result
  *right now in this reply* and it's small.
- **Just answer** — if it's a code *question* or a few lines to paste, don't dispatch anything.

Rule of thumb: if it needs its own workspace and more than a couple of minutes of grinding, it's
Chad Code. If it fits in this reply, it's `code_agent` or a direct answer.

## Standard Operating Procedure

### 1. Gather context FIRST (this is the whole point)
Chad Code's local model can't reach your Gmail, Drive, Notion, or the web. Anything the task
depends on, **you** must fetch and bake into the brief before dispatching. Common moves:
- Spec / requirements doc in Drive or on disk → if you already have a Drive `file_id` / local
  path (memory, user, prior turn), `google_drive_read_file` / `read_file` yourself; if the
  location is unknown or needs explore/search-through, `delegate_to_traversal_agent`.
- A decision or details in email → `search_gmail` then `read_gmail` yourself.
- API docs / unknowns on the web → `delegate_to_deep_research_agent`.
- An existing codebase to work against → mount it (see `reference` / `workspace` below), don't
  paste the whole thing.

Do this gathering in your own turn, then hand the *distilled* result to Chad Code. Don't make the
coding model guess at things you could have looked up.

### 2. Write a self-contained brief (the `task` string)
The single biggest lever on local-model reliability. A good brief states:
- **What to build/change** — concrete deliverable, not a vibe.
- **Constraints** — language, framework, file layout, style, what NOT to touch.
- **Context you gathered** — paste the relevant spec text / values inline (keep it tight).
- **How to verify** — the command or check that proves it works (`pytest -q`, "runs `python
  main.py` and prints X"). Chad Code has **no built-in success signal**, so tell it how to know
  it's done.

Think "a brief a competent stranger could execute with zero access to your accounts."

### 3. Dispatch
Call `dispatch_coding_task` (requires approval — it writes files and runs a model):
- `task` — the brief from step 2. **Required.**
- `title` — short label for the job.
- `workspace` — an existing host dir to work in (continue prior work / write into a real project).
  Omit for a fresh empty workspace. A workspace inside a git repo is refused unless allowlisted
  (safety: the monorepo isn't writable by default).
- `reference` — a host dir mounted **read-only at `/repo`** to explore/reference without touching
  it. Use this for "understand this codebase and produce X" — output goes to the separate writable
  workspace.
- `max_iterations` — raise for bigger tasks (default 30); lower to fail fast on a probe.
- `enable_github` — set **True only** when the task genuinely needs GitHub (read issues/PRs, open
  a PR, search a repo's code). It adds the GitHub toolset (~44 tools). **Leave it off for ordinary
  coding** — those tools are dead weight and dilute the coder's focus on a build/edit task.

You get back a `job_id`. Tell the user it's running and that you'll report back — then continue;
don't sit and poll.

### 4. Check / stop
- `check_coding_task(job_id)` → status (running / finished / failed), transcript tail, workspace
  file list. Summarize the outcome for the user; surface the files it produced.
- `stop_coding_task(job_id)` → kill a stuck or looping job; workspace files are kept.

## Toggles (defaults are usually right — leave them unless asked)
Configured in **Settings → Services → Chad Code** (or `config.yaml` `tools.openhands_*`):
- **max_iterations** (30) — per-call budget; also a `dispatch_coding_task` param.
- **job_timeout_s** (1800) — wall-clock cap; the job self-kills past it.
- **native_tool_calling** (OFF) — keep OFF for mid-size local coders (they emit Hermes-style
  `<function=…>` text, not native tool_calls; ON breaks them). Only flip ON for a model you know
  emits real tool_calls.
- **task_tracker** (OFF) — OpenHands' internal planning scaffold; off by default (A/B'd as noise
  for small models). Turn ON only for a genuinely multi-phase task.

## What Chad Code brings automatically (you don't set these per-dispatch)
- **Self-review** — the coder loads a `code-review` skill: it re-reads its diff, checks edges,
  and runs the verification you gave before finishing. So *always* give a verification step.
- **Docs lookup** — Context7 (`query-docs`) is always available so the coder can check current
  library/API docs instead of guessing. Web search (Brave) is available via a `web-search` skill.
- **git discipline** — a `github-workflow` skill (branch, small commits, no force-push). The full
  GitHub *toolset* is only attached when you pass `enable_github=True` (see Dispatch above).
- **Live progress** — each agent step is streamed, so a job is watchable, not a black box.
  You can tell the user "it's working, here's what it's done so far" from `check_coding_task`.

## Prompting tips for the local coder
- **One clear objective per job.** Don't cram three unrelated features into one brief.
- **Be explicit about file paths and names** — small models drift without them.
- **Give the verification command** — it's the closest thing to a done-signal.
- **Prefer `reference` over pasting** a large codebase; paste only the specific snippet that matters.
- **Fresh workspace for greenfield**, `workspace` for continuing, `reference` for explore-and-report.

## Common pitfalls
- ❌ Dispatching before gathering the context the task depends on (the model can't fetch it).
- ❌ A vague brief with no verification step → the job "finishes" having built the wrong thing.
- ❌ Blocking your turn waiting on the job — it's fire-and-report.
- ❌ Pointing `workspace` at the Chad monorepo (refused by design; use a scratch dir or allowlist).
- ❌ Flipping `native_tool_calling` ON for a mid-size local model (it stops executing).

## Prerequisites
Docker Desktop running + the OpenHands image pulled + LM Studio serving the coder model.
`dispatch_coding_task` preflights these and returns an actionable error if something's missing;
the GUI tab has a one-click preflight/setup. If preflight fails, tell the user exactly what to
start (Docker / LM Studio / the model download) rather than retrying blindly.
