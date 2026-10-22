# Private Data Architecture

This repo is public. All user-specific data stays on the local machine and is excluded from git.
This doc explains what's private, where it lives, and how it reaches the running application.

The proactive-memory candidate filter uses portable preference, relationship and
directive cues. It must not embed the maintainer's contacts or places in source.
Its extraction pipeline is proposal-only and is not registered to run after chat
turns. Legacy learn directives cannot persist facts without desktop approval.
Contact names in evaluation examples use fictional aliases; anonymizing current
files does not remove older commits, GitHub discussions or released binaries.

## The three layers

### 1. Secrets — API tokens, OAuth credentials, service keys

**Local file:** `config/secrets.yaml` (gitignored)
**Template:** `config/secrets.yaml.example` (tracked — documents required keys)

`ConfigManager` loads `secrets.yaml` at startup and merges it with `config/config.yaml`.
In `config.yaml`, secret values use `%ENV_VAR_NAME%` placeholders:

```yaml
mcp_servers:
  notion:
    env:
      NOTION_API_TOKEN: "%NOTION_API_TOKEN%"
```

`ConfigManager.expand_path` calls `os.expandvars`, so `%NOTION_API_TOKEN%` resolves to
the value from `secrets.yaml` (which sets it as an env var) or the system environment.

`system_factory.py` also expands `%VAR%` tokens in MCP `command:` arrays, not just `env:`.

**Setup:** `cp config/secrets.yaml.example config/secrets.yaml` then fill in real values.

---

### 2. Personal knowledge — user profile, facts, relationships

**Local file:** `config/personal_knowledge.md` (gitignored)
**Template:** `config/personal_knowledge.md.example` (tracked)

This is the single source of truth for what Chad knows about the user: identity, family, health,
career, finances, hardware, interests, projects, routine. Written in plain English; each `##`
section becomes a node in the knowledge graph.

`GraphRAGClient` reads this file at startup, builds `data/knowledge_graph.json` (also gitignored),
and uses it for semantic retrieval during conversations.

**Setup:** `cp config/personal_knowledge.md.example config/personal_knowledge.md` then fill in
your real information. The runtime graph rebuilds automatically on next startup.

---

### 3. Runtime knowledge graph

**Local file:** `data/knowledge_graph.json` (gitignored)

Auto-generated from `personal_knowledge.md` by `GraphRAGClient`. Never edit directly.
Deleted or stale graphs regenerate on the next server startup.

---

### 4. Personal preferences overlay (`preferences.yaml`)

**Local file:** `config/preferences.yaml` (gitignored)
**Template:** `config/preferences.yaml.example` (tracked)

Runtime/GUI settings (model picker, MCP toggles, daemons) write here automatically.
Machine-specific profile data (apps, routines, monitor layout) goes under the `profile:`
key — deep-merged over the tracked `profiles.yaml` skeleton.

**Setup:** `copy config\preferences.yaml.example config\preferences.yaml` then customize.
Or run `.\scripts\setup\bootstrap_config.ps1` from a fresh clone.

---

## What's expanded at runtime

| Pattern | Where | Expands to |
|---|---|---|
| `%USERPROFILE%` | `config/config.yaml`, `config/profiles.yaml`, `config/preferences.yaml` (`profile:`) | Windows home directory (`C:\Users\<name>`) |
| `%NOTION_API_TOKEN%` | `config/config.yaml` MCP env | Value from `secrets.yaml` or system env |
| `%GOOGLE_DRIVE_OAUTH_CREDENTIALS%` | `config/config.yaml` MCP env | Absolute path to OAuth JSON file |
| Any `%VAR%` | MCP `env:` or `command:` arrays | `os.expandvars` in `ConfigManager` / `system_factory.py` |

---

## Rules for keeping the repo clean

1. **No real names in tracked files.** Use `the user`, `Alex Example`, `your-app.onrender.com`.
2. **No real emails.** Use `user@example.com` or omit entirely.
3. **No hardcoded tokens.** Every secret goes in `secrets.yaml` or an env var; config references it via `%VAR%`.
4. **No absolute personal paths.** Use `%USERPROFILE%\...` so any user's machine works.
5. **Tests use fictional data.** No real names, emails, service URLs, or health info in test fixtures.
6. **Deployment URLs stay local.** Render app names, Supabase project refs, ngrok URLs — all in `secrets.yaml` or env.

---

## What to do if sensitive data leaks into a commit

1. **Revoke the credential immediately** — removing it from the file does not invalidate it if it's in history.
2. **Purge from git history** using `git filter-repo` (see issue #212 for the full procedure).
3. **Force-push** after the rewrite — coordinate with anyone who has cloned the repo.
4. **Add to `.gitignore`** so it can't happen again.

The pre-commit hook (issue #212, not yet installed) will run `gitleaks` to catch this before a commit lands.

---

## Gitignored files reference

```
config/secrets.yaml              # API tokens and credentials
config/preferences.yaml          # Runtime/GUI overrides + profile: machine overlay
config/personal_knowledge.md     # User profile and personal facts
data/knowledge_graph.json        # Runtime graph built from personal_knowledge.md
data/memories_flatfact_backup_*  # Memory consolidation backups (contain personal data)
data/*.db                        # SQLite databases (incl. local_chad.db: Activity + Drive catalog)
.env / .env.local                # Environment variable overrides
credentials.json / token.json    # Google OAuth tokens
```

`data/local_chad.db` holds Activity (`personal_index`: Chrome/Gmail/Drive activity/
filesystem catalog) and Drive location catalog rows. These stay **local-only** —
they are never mirrored to Supabase. Treat the DB like personal data — never
commit it. See [LOCAL_FIRST.md](../setup/LOCAL_FIRST.md) and
[BROWSER_HISTORY.md](../setup/BROWSER_HISTORY.md).
Optional Supabase backup (when enabled) is limited to personal knowledge-graph
facts and persona prompt overlays — not this Activity corpus.
