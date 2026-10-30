# Developer workspace (local-only files)

This repo is public, but some paths are **gitignored** so maintainers can keep personal dev
harness files locally without publishing them. Same pattern as `config/secrets.yaml` (local) vs
`config/secrets.yaml.example` (tracked template).

Inspired by [OpenClaw's agent workspace model](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-workspace.md):
product code stays public; private working memory and agent instructions stay on your machine
(optionally backed up in a **private** git repo).

Shared project instructions are tracked in [AGENTS.md](../../AGENTS.md).
Local files add personal preferences; a clean clone does not need `RTK.md`.

## First-time setup

From the repo root:

```powershell
.\scripts\setup\bootstrap_config.ps1
```

That copies config templates **and** `CLAUDE.md.example` → `CLAUDE.md` when missing.

To refresh only the dev workspace files:

```powershell
.\scripts\setup\bootstrap_dev_workspace.ps1
```

## Local-only paths (never committed)

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Local preferences; links to the shared AGENTS.md. |
| `CONTEXT.md` | Domain vocabulary and deployment notes for agents. |
| `TODOS.md` | Personal idea scratch pad. |
| `.agent/` | Agent rules, skills, workflows (e.g. `.agent/skills/connector-setup/` for new MCP/native connectors). |
| `.claude/` | Local launch configs; shared skills/agents/commands are tracked. |
| `opencode.json` | OpenCode / local LM Studio settings. |
| `.cursor/` | Cursor rules (already gitignored). |
| `docs/HANDOFF_*.md`, `docs/SESSION_*.md` | Session working notes. |
| `evals/datasets/wedding_*.jsonl` | Personal workflow eval cases. |
| `config/secrets.yaml`, `personal_knowledge.md`, `preferences.yaml` | Runtime secrets and profile — see `PRIVATE_DATA.md`. |

## Tracked templates (public)

| Path | Purpose |
|---|---|
| `CLAUDE.md.example` | Slim contributor/agent starter — copy to `CLAUDE.md`. |
| `config/*.example` | Secrets, preferences, personal knowledge templates. |

## Backing up your dev workspace

Git will not track the files above. To sync across machines:

1. **Private repo** (recommended): init a private repo elsewhere (e.g.
   `~/dev/chad-dev-workspace/`), copy or symlink `CLAUDE.md`, `.agent/`, etc., and push to
   GitHub/GitLab as **private**.
2. **Dotfiles / cloud drive**: copy the gitignored paths manually.
3. **Do not** push `.agent/` or `CLAUDE.md` to the public Chad repo — even if the content looks
   harmless, it bloats the project and may reference personal workflow details.

## Contributors

You do not need a full `CLAUDE.md` or `.agent/` tree to contribute. Read
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) and copy `CLAUDE.md.example` if you use AI coding
tools. CI runs the free eval lanes documented there — no maintainer-only skills required.
Workflow definitions live in `.github/workflows/`; check the PR’s actual runs for status.

## Mid-session worktrees

A SessionStart hook does not necessarily run again when a tool creates a new
worktree mid-session. Before running checks that require credentials, verify
that the required local config exists in that worktree. Missing OAuth files can
look like revoked credentials. Never print credential contents or overwrite an
existing local file; use your private workspace setup to supply missing files.
Deterministic documentation checks need no secrets.

## Existing-profile testing

Normal branch testing uses the maintainer's main local profile. Repeated launches
have accidentally opened **Welcome to Chad** because the dev setup shared memory
and secrets but omitted `config/preferences.yaml`, and legacy onboarding required
the default model catalog instead of accepting the existing selected model.

`scripts/prod/setup_dev_worktree.ps1` now shares preferences alongside credentials
and personal knowledge, and uses the main database's absolute path. Divergent
local files are backed up before linking. Preferences changes in dev are therefore
changes to the main profile. Persona experiments retain an independent copy.
Set `CHAD_MAIN_ROOT` to the main checkout; `-DevRoot <branch-checkout>` prepares any
existing checkout, while the default prepares the usual sibling dev checkout.
The dev launcher sets `CHAD_DEV_WORKTREE=1`; setup recognizes legacy model choices
only when the preferences file is actually shared with that main checkout.

Fresh-install tests must be explicitly requested and run in a separate clean
profile without these links, shared database settings, or dev-profile markers.
Never point `CHAD_HOME` at main to reuse data: that can select main's code/assets.
Before reporting a launch ready, check the actual GUI shows the existing workspace
and confirm the Brain is on 8788. A health response alone misses onboarding.
Use `scripts/prod/launch_dev.ps1 -CurrentCheckout` to run the current branch's
Brain and UI with that profile. Without this option the legacy launcher redirects
to the sibling dev checkout, which can run a different repository and old UI.

For source startup commands use [cold start](../setup/COLD_START.md). Keep runtime
entrypoints in [the project map](../architecture/END_TO_END.md), not local copies.
