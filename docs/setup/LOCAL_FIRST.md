# Local-First Mode

Chad runs fully offline with no cloud dependency by default. Every subsystem
that talks to a database routes through `SupabaseManager`
(`src/infrastructure/database/supabase_manager.py`): if `SUPABASE_URL` and
`SUPABASE_ANON_KEY` are unset (or empty), `SupabaseManager.enabled` is
`False` and every read/write is delegated to `LocalPostgrestShim`
(`src/infrastructure/database/local_postgrest.py`), a SQLite-backed
PostgREST-compatible shim. Nothing forks between a "local mode" and a "cloud
mode" code path — the exact same repository/service code runs either way.

Configuring `SUPABASE_URL`/`SUPABASE_ANON_KEY` (e.g. in `.env`) switches
`SupabaseManager` back to a real Supabase project — useful for multi-device
sync, multi-user setups, or if you'd rather manage your data in Postgres.

## Models (local by default)

Committed `config/config.yaml` ships **local models as the default** (#448):

- `local_models.use_local: true`
- `local_models.active_model: google/gemma-4-26b-a4b-qat` (supervisor / chat)
- Fast aux roles (`tools.llm_selector_model`, voice category, router/thinking
  fallbacks) use `qwen/qwen3.5-9b`
- Category entries under `models:` also point at those LM Studio ids (with
  `base_url: http://localhost:1234/v1`) so even the `use_local: false` fallback
  path does not silently call Gemini

Cloud providers appear only in `available_cloud_models` for the Settings picker.
Selecting one opts into cloud (`use_local: false`). A fresh clone with no
`preferences.yaml` / no API keys talks to LM Studio only.

## What Supabase is for (and what it isn't)

Chad started cloud-first: Supabase held the persona, the memory, the history —
and an install with no Supabase project simply refused to boot. That inverted
over issues #403 / #416 / #462 / #465 / #589, and the current position is worth
stating plainly because a lot of older docs and code comments still assume the
old world.

**Supabase is an optional backup / sync mirror. It is never the primary store for
memory, catalogs, or personal knowledge — and never a source of truth for behavior.**

| Supabase IS for | Supabase is NOT for |
|---|---|
| Opt-in multi-device **backup** of personal knowledge graph + persona overlays when `knowledge_graph.storage: local_then_supabase` | **Primary memory / Activity / catalogs.** Bulk Chrome, Gmail, filesystem, and Drive catalog rows stay on local sqlite forever |
| Scale (pgvector) if you choose to mirror a *small* personal-fact corpus | **Boot dependency.** No Supabase project is a fully supported, first-class configuration |
| Multi-user installs that need real auth isolation | **Anything you'd want to review in a PR.** If a change should be diffable, it belongs in a file |
| Preferring to also keep a Postgres copy of *identity* facts / prompts | **High-volume Activity.** Browser history, mail snippets, filesystem/Drive catalogs are never mirrored |

The dividing line: **configuration and behavior live in files; bulk accumulated
activity stays on-disk; only small personal identity (graph + prompts) may
optionally back up to Supabase.** Prompts are behavior, so files remain the
source of truth even when a cloud copy exists for multi-device restore.

Practically, this means a contributor can clone the repo and get *the same Chad
the maintainer develops against*, rather than a knowingly-degraded variant —
which was the actual failure #589 fixed.

## Status matrix

| Feature | Storage | Notes |
|---|---|---|
| Persona prompts | Local (files) primary | `prompts/persona/*.md` baseline + `config/persona/*.md` overlay — issue #589. Optional Supabase *backup* of overlays when `knowledge_graph.storage: local_then_supabase` (files remain SoT). See [PERSONA_PROMPTS.md](../architecture/PERSONA_PROMPTS.md) |
| Personal knowledge graph | Local (files) primary | `config/personal_knowledge.md` + `data/knowledge_graph.json`. `knowledge_graph.storage`: `local` / `local_then_supabase` (mirror facts to Supabase after save). OSS ships `.example` template only. |
| Memory / Activity (`personal_index`) | Local (SQLite) **only** | Chrome, Gmail, Drive activity, filesystem catalog — never mirrored to Supabase (PRs #621/#622). `memory.storage: local` (default unset too); `supabase` escape hatch still forces bulk sources onto sqlite. See [MEMORY_AND_CONTEXT.md](../architecture/MEMORY_AND_CONTEXT.md) |
| Drive catalog (`drive_catalog`) | Local (SQLite) **only** | Name + folder path + file id for “where is that doc?” — never mirrored. Body reads stay on-demand via `google_drive_read_file`. Reconcile refuses empty/high-ratio deletes. |
| Cron / scheduled agent tasks | Local (SQLite) | `TaskRepository` — issue #466 |
| Reflection lessons | Local (SQLite) | `reflection_lessons_store` — issue #467 |
| Kanban board | Local (SQLite) | `KanbanRepository` — issue #468 |
| Workflows (saved tool-call automations) | Local (SQLite) | `WorkflowRepository` — issue #468 |
| Dashboard feed | Local (SQLite) | `dashboard_feed` — issue #469 |
| OATS tool-selection refinement | Local (SQLite) | `scripts/oats_refine.py` — issue #470 |
| Telegram | State local / transport cloud | Routing/session state is local (`TelegramChatRepository`, issue #471); the Telegram Bot API itself is an inherently cloud-hosted transport and can't be localized |
| Multi-user auth | Cloud (Supabase only) | Requires a real auth provider with per-user isolation; not localized — single-user installs don't need it |

## Proof

`tests/integration/test_local_first_subsystems.py` runs all six SQLite-backed
subsystems above against one shared on-disk database with `SUPABASE_URL`
unset, proves they coexist without table/registration collisions, and
simulates a Brain restart (reconnecting to the same file with a fresh
`SupabaseManager` singleton) to confirm every write survives.
