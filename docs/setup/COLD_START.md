# Cold-Start Setup (fresh clone → running Chad)

> **Scope note:** this doc tests the **dev/source path** (`git clone` → venv →
> `python src/remote/server.py`) — useful for OSS contributors and anyone
> comfortable with a terminal, and what issue #409's dry run currently
> exercises. It does **not** require the Windows installer (#218) and isn't
> the "friend downloads and double-clicks" experience — that's a separate,
> stricter bar tracked directly in #218's acceptance criteria (single `.exe`,
> no Python/Node, first-run wizard instead of hand-edited `secrets.yaml`).
> Both paths matter; don't conflate a passing #409 dry run with the installer
> being done.

> **Status: PARTIALLY VERIFIED.** This is the exact path a new user (a friend, or
> an OSS visitor) follows from `git clone` to a working Chad. It doubles as the
> test script we walk through end-to-end before flipping the repo public. The
> zero-Supabase local fallback (#403) was verified directly — see "Verified"
> below. The optional Supabase upgrade path's SQL was cross-checked
> statement-by-statement against the live schema but not yet run against a
> truly empty, brand-new project (the maintainer's org was at its 2-free-project
> quota when this was written). Every remaining step marked ⚠️ is a known
> friction point / open question to confirm on a clean box.

The honest question this doc answers: **can someone who is not Matthew run Chad?**

The environment template leaves optional credentials commented out. First-run
setup copies it unchanged; local SQLite history must remain active until real
Supabase settings are deliberately supplied. Do not uncomment example tokens
or project URLs: nonempty placeholders can enable an unusable cloud backend.

**Philosophy:** the thing OpenClaw gets wrong is that a genuinely capable local
assistant takes forever to stand up. Chad's default path is **local-first,
zero-config** — clone, fill in one `.env`, run. Supabase (cloud persona editing,
vector RAG at scale, cross-device chat history) is an **optional upgrade**, not
a precondition for Chad to work at all.

---

## Components a fresh user needs

| Component | Required? | How it's provided | Cold-start risk |
|---|---|---|---|
| Python 3.12 + venv + `requirements.txt` | Yes | `pip install -r requirements.txt` | Low |
| Node + web bundle (`src/clients/web`) | Yes | `npm install && npm run build` | Low |
| Cloud LLM key | No | Optional cloud provider configuration in `config/secrets.yaml` | Not needed for local chat |
| Persona/prompts | Yes | **Built in, zero config** — local files, `prompts/persona/*.md` (#403, #589) | ✅ Verified |
| **Supabase project** (richer memory/RAG, chat history sync) | **No — optional upgrade** | user provisions own project + runs one bootstrap script | Low — SQL cross-checked, live run pending a free project slot |
| LM Studio (local tool-selector 9B + embeddings) | Recommended | user installs, loads models | ⚠️ Medium |
| Docker services (SearXNG, Presenton, Kroki…) | Optional (tiered) | `docker/chad-services.yml` | Low — audited #405, every sidecar has a working fallback (live compose-up dry run still pending) |
| Voice / STT / wake word | Optional | `requirements-voice.txt`, models | Low (off by default) |

---

## ✅ Verified: Chad boots and holds a conversation with zero Supabase config (#403, #589)

Chad's persona/protocol prompts historically lived ONLY in Supabase's
`persona_config` table (the "Cloud Master"), warmed by `PromptRegistry`. A
fresh user with no Supabase configured got `PromptRegistry.warmup()` raising
and Chad refusing to boot at all — the single biggest cold-start blocker.

#403 added a bundled generic persona as a last-resort fallback behind Supabase.
**#589 finished the job: prompts are now local files on every tier, and Supabase
is never consulted for them at all.**

- **`prompts/persona/*.md`** — a generic, non-personal fragment for each of the
  24 scopes the persona uses. Committed to the repo, so a fresh clone has a
  complete working persona with zero setup.
- **`config/persona/*.md`** — an optional per-user overlay that overrides the
  baseline per fragment id. Gitignored. This is where your own evolved prompts
  live, and where the reflection loop and persona editor write.
- **`PromptRegistry.warmup()`** reads those two directories and nothing else.
  There is no fallback chain left to get out of order.
- **Verified directly** — `PromptRegistry.warmup()` + `SystemPromptBuilder.build()`
  produce real system prompts for every scope (supervisor 14283 chars,
  conversational 6408, planner 5946, browser_agent 8388, validator 3455, scout
  5001) with no database involved.

See [PERSONA_PROMPTS.md](../architecture/PERSONA_PROMPTS.md) for the full model,
and run `python evals/runner.py --persona-parity` to verify the shipped baseline
is complete.

Tool RAG, long-term memory (vector search), and cross-device chat history
sync still require Supabase — see "Local-only mode" below for what's tracked
to close that gap too.

### Optional: Supabase upgrade path

Only if you want richer memory/RAG. **Persona prompts are not part of this** —
they are local files regardless of whether Supabase is configured:

1. Create a free project at [supabase.com](https://supabase.com) (any region).
2. Dashboard → Settings → Data API: copy the Project URL into `.env` as
   `SUPABASE_URL`. Settings → API Keys: copy the `anon`/publishable key into
   `SUPABASE_ANON_KEY` and the `service_role`/secret key into
   `SUPABASE_SERVICE_KEY`. Settings → Database → Connection string (URI,
   "Session" pooler mode): copy it into `.env` as `SUPABASE_DB_URL`. All four
   go in **`.env`**, not `secrets.yaml` — `supabase_manager.py` reads them via
   `os.getenv`, and only `.env` is loaded into the process environment.
3. Run the one-command bootstrap — applies every migration (all tables + the
   `vector` extension + the RPC functions tool RAG/memory search need,
   including `src/infrastructure/database/migrations/013_cold_start_baseline_schema.sql`,
   which captures everything migrations 001-012 never committed) and seeds
   tool RAG (discovers
   local + configured MCP tools, syncs embeddings via
   `nomic-ai/nomic-embed-text-v2-moe` locally through `sentence-transformers` —
   no LM Studio needed for this step):
   ```powershell
   $env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts\setup\bootstrap_supabase.py
   ```
   (LangGraph's checkpoint tables — `checkpoints`, `checkpoint_blobs`,
   `checkpoint_writes` — are NOT part of this; they're created automatically
   the first time the Brain boots against Postgres.) Safe to re-run — every
   step is idempotent. Prefer pasting SQL by hand? Run every file in
   `src/infrastructure/database/migrations/` in numeric order via the
   dashboard SQL Editor instead, then run `src/scripts/refresh_rag_tools.py`
   separately.

   **Upgrading an install that predates #589?** If your persona still lives in
   Supabase's `persona_config`, pull it local once:
   ```powershell
   $env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts\setup\migrate_persona_to_local.py --dry-run
   ```
   Drop `--dry-run` to write. Supabase rows are left intact as a backup.

   ⚠️ **Known gap:** `refresh_rag_tools.py` scans every server under
   `mcp_servers` in `config.yaml` (playwright, brave-search, todoist, github,
   …). As of #403, `refresh_rag_tools.py` **skips** disabled servers and
   servers missing credentials — only credentialed servers that fail to connect
   abort the sync (guards against partial scans deleting live tools).

**GUI path (Settings → Memory → Cloud Memory):** paste your Supabase URL, keys,
and database connection string, then click **Connect & provision**. This runs
the same bootstrap as the script below. The CLI path still works if you prefer
terminal setup.

---

## ✅ Full local-only mode (tool RAG, memory, chat history, turn state)

Done (issue #416). With no `SUPABASE_URL`/`SUPABASE_ANON_KEY` in `.env`, every
subsystem below transparently falls back to a local sqlite file
(`data/local_chad.db`, `data/local_langgraph_checkpoints.db`) auto-created on
first use — nothing to configure or run. Embeddings are already computed
locally via `sentence-transformers` regardless of backend, so the only thing
that changes is where vectors/rows are stored and how they're searched.

| Subsystem | Supabase | Local-only fallback |
|---|---|---|
| Tool RAG (tool selection) | pgvector + full-text hybrid RPC (`retrieve_relevant_tools`) | Local sqlite cache of per-tool embeddings + brute-force numpy cosine (`SemanticToolSelector._query_local_rag`) — vector-only, no FTS/RRF fusion, no OATS-refined embeddings |
| Long-term memory | `memories` table + `match_memories` RPC | Local sqlite + numpy cosine (`MemoryRepository`) |
| Chat history/conversations | `conversations`/`messages` tables | Local sqlite (`ChatHistoryRepository`) — no cross-device sync |
| Turn/graph state | LangGraph `AsyncPostgresSaver` | `langgraph-checkpoint-sqlite` (`SafeAiosqliteSaver`) — durable across restarts, unlike the ephemeral `SafeMemorySaver` used only as a connection-failure fallback |

Every one of these branches on the same single switch point,
`SupabaseManager.enabled` — no new config plumbing. Performance-wise, local
mode removes a network round-trip per call (checkpointing in particular pays
this on every graph step against Supabase), so it's often *faster* per-call
on a single machine; the tradeoffs are no cross-device sync and a somewhat
weaker tool-RAG ranking (vector-only vs. Supabase's hybrid FTS+vector fusion).
At the row counts a single-user install ever reaches (hundreds of tools,
low-thousands of memories), brute-force cosine is low-single-digit-ms — no
indexed ANN structure needed.

As of #465, `SupabaseManager`'s disabled path itself delegates to a generalized
`LocalPostgrestShim` (`src/infrastructure/database/local_postgrest.py`) rather
than each repository hand-rolling its own sqlite branch. It translates
PostgREST filter grammar (`eq.`/`ilike.*x*`/`or=(...)`/`order`/`limit`/etc.)
into parameterized SQL against `data/local_chad.db`, and new subsystems opt in
by calling `register_table(name, columns, pk)` once — no per-feature `if not
self.supabase.enabled` branching required going forward. `memory_repository.py`
predates this and still hand-rolls its own local branch; new local-first slices
(#462) build on the shim instead.

Telegram, kanban, workflows, capability self-improvement, and OATS are
optional/advanced features; several now have local sqlite fallbacks via
`LocalPostgrestShim` but are not part of the cold-start path. See
[OSS_SETUP_GAPS.md](OSS_SETUP_GAPS.md) for the full audit.

---

## Setup steps (to validate on a clean machine)

1. **Clone & Python env**
   ```powershell
   git clone https://github.com/MatthewLoyed/Chad.git chad
   cd chad
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. **Config** — bootstrap gitignored templates (or copy manually):
   ```powershell
   .\scripts\setup\bootstrap_config.ps1
   # Then fill config\secrets.yaml and .env (GEMINI_API_KEY minimum for cloud mode).
   ```
   Tracked `config.yaml` / `profiles.yaml` ship generic defaults. Personal apps,
   routines, monitor layout, and fast-track shortcuts go in
   `config/preferences.yaml` under `profile:` (see `preferences.yaml.example`).
   **Personal knowledge** — `bootstrap_config.ps1` also copies
   `personal_knowledge.md.example` → `personal_knowledge.md`. Edit that file so Chad
   knows who you are (see [PERSONAL_KNOWLEDGE.md](PERSONAL_KNOWLEDGE.md)).
   Connectors (MCP, Gmail, Telegram, Canva, …) are **opt-in** — see [CONNECTORS.md](CONNECTORS.md).
   Canva graphic design uses the same Connectors wizard as Supabase (browser OAuth via `mcp-remote`).
   Supabase variables in `.env.example` can stay as placeholders — Chad boots
   without them (see above). Fill them in only for the optional Supabase upgrade.
3. **LM Studio** — install LM Studio (adds its `lms` CLI to PATH), then run:
   ```powershell
   python scripts/setup/setup_lmstudio.py
   ```
   This checks for `lms`, downloads the three required models
   (`google/gemma-4-26b-a4b-qat` main/judge, `qwen/qwen3.5-9b` tool selector,
   `google/gemma-3-1b` intent router — confirmed downloadable LM Studio catalog
   names) with a confirmation prompt, and starts the local server. See
   `docs/features/LOCAL_MODELS.md` for the full required/optional table, 12GB-VRAM
   guidance, and the manual fallback if `lms` isn't available. Embeddings
   (`nomic-embed-text-v2-moe`) are **not** an LM Studio model — they run locally via
   `sentence-transformers`, no action needed here.
4. **Docker services (optional)** — see [Optional services (Docker)](#optional-services-docker) below.
   ```powershell
   docker compose -f docker/chad-services.yml up -d
   .\scripts\setup\verify_chad_services.ps1
   ```
5. **Build web UI**
   ```powershell
   cd src/clients/web; npm install; npm run build; cd ../../..
   ```
6. **Run the Brain**
   ```powershell
   $env:PYTHONPATH="."
   # Optional: $env:CHAD_DAEMONS_ENABLED="false"  # disables background daemons (heartbeat, cron, ...)
   .\.venv\Scripts\python.exe src\remote\server.py
   ```
   Confirm `GET http://localhost:8767/health` → `ok`. Daemons (heartbeat, cron) are **on by
   default** — only set `CHAD_DAEMONS_ENABLED=false` if you want a minimal dev Brain with no
   background workers. Browser-history sync is a separate opt-in capability (off by
   default even with daemons on) — see step 10.
7. **Run the GUI (Limb)**
   ```powershell
   $env:CHAD_UI_DEV="false"; $env:PYTHONPATH="."
   .\.venv\Scripts\python main_gui.py
   ```
8. **Smoke test** — send "what time is it?" and one tool-using prompt
   (e.g. a web search). Confirm a reply renders and a tool runs.
9. **(Optional) Personalize memory** — edit `config/personal_knowledge.md` and confirm
   Settings → Memory → **User Facts** populates after restart. See
   [PERSONAL_KNOWLEDGE.md](PERSONAL_KNOWLEDGE.md).
10. **(Optional) Browser history** — off by default. If you use Chrome on Windows and want
    it, enable it in Settings → **Capabilities** (or ask Chad to refresh browser history
    once for a one-time pull). Verify Settings → Memory → **Activity**. See
    [BROWSER_HISTORY.md](BROWSER_HISTORY.md).
11. **(Optional) Supabase upgrade** — see the section above if you want it.

---

## Optional services (Docker)

Nothing in this section is required to run Chad — every sidecar has a public/managed
fallback, and Chad boots and works fully without Docker installed at all. Self-hosting
just removes rate limits and keeps data local.

Config wiring and fallback behavior below were verified by code inspection for issue #405
(every path traced from `config.yaml` through to the tool that uses it). A live
`docker compose up -d` + `verify_chad_services.ps1` dry run on a genuinely clean machine is
still worth doing — the attempt during #405 hit an unrelated, pre-existing Docker Desktop
bug on the test machine (a locked stale socket file blocking its daemon from starting) and
had to be deferred; it's a Docker Desktop install issue, not a Chad or compose-file problem.

| Service | Config key | Default (no Docker) | With `docker/chad-services.yml` up |
|---|---|---|---|
| SearXNG (web search) | `tools.searxng_url` | Falls back to Brave (`tools.search_backend: searxng` tries local first, logs a warning, then retries via `brave_web_search`) | `http://127.0.0.1:8080` |
| Kroki (diagrams) | `visual.kroki_url` | Public `https://kroki.io` (default out of the box — no Docker needed) | `http://127.0.0.1:8000` |
| Presenton (AI decks) | `visual.presenton_url` | Disabled (`null`) — tool reports unavailable, no crash | `http://127.0.0.1:5000` (opt-in via Settings → Services, or `--profile presenton`) |

Start / verify / stop:
```powershell
docker compose -f docker/chad-services.yml up -d       # searxng + valkey + kroki (presenton/excalidraw are separate --profile flags)
.\scripts\setup\verify_chad_services.ps1                # same checks as Settings -> Services in the GUI
docker compose -f docker/chad-services.yml down
```

Notes from the #405 audit:
- `verify_chad_services.ps1` and the GUI's Settings → Services panel (`GET /api/services/health`,
  issue #363) read the exact same tiers (Core/Enhanced/Optional) — they will never drift out of
  sync with each other, only one to keep current.
- With the compose stack down, `web_search` still works (falls back to Brave), `render_diagram`
  still works (falls back to public `kroki.io`), and Presenton simply stays off — no failure
  anywhere in the Core/Required path. Docker is genuinely optional.
- SearXNG needs `docker/searxng/settings.yml` (checked into the repo, mounted read-only) — no
  extra setup beyond `docker compose up`.
- Presenton needs `docker/presenton/.env` (copy from `.env.example`) before its container is
  useful; without it the container starts but has no LLM configured. It's gated behind
  `--profile presenton` so a plain `docker compose up -d` never pulls its (larger) image.

---

## Cold-start acceptance criteria (the launch gate)

- [ ] A machine that has never run Chad completes steps 1–8 using **only this doc**
      and **without touching Supabase at all**.
- [x] Persona boots with zero Supabase config (#403 — verified).
- [ ] Personal knowledge graph documented and customizable (#492 — `PERSONAL_KNOWLEDGE.md`).
- [ ] Browser/activity history documented and verifiable (#493 — `BROWSER_HISTORY.md`).
- [ ] Tool RAG / memory / chat history work locally too (tracked follow-up).
- [ ] No step requires Matthew's Supabase, accounts, or personal files.
- [ ] Every default in `config.yaml` resolves on a generic Windows box.
- [ ] Failure modes are legible (clear error if LM Studio missing; Supabase
      absence is no longer a failure mode).
- [ ] Time-to-first-reply from clone is documented (rough minutes).

Findings from each dry run go here as we test.

**#405 (Docker sidecars)** — config wiring and fallback code paths audited and confirmed
correct (see "Optional services (Docker)" above). Live `docker compose up -d` +
`verify_chad_services.ps1` bring-up was attempted but blocked by a Docker Desktop bug on the
test machine (stale locked socket file, unrelated to Chad); still needs a live dry run on a
genuinely clean machine to fully close this item.
