# Chad Code — local-model coding harness (OpenHands-backed)

**Epic:** [#569](https://github.com/MatthewLoyed/AIAssistant/issues/569) · **Foundational:** [#447](https://github.com/MatthewLoyed/AIAssistant/issues/447)
**Status (2026-07-21):** headless path built + merged (#577). **Tiers 1–3 all PASS** with the
local 35B via the #573 eval probe — single-file game, multi-file full-stack (self-correcting test
loop), and a hard multi-module CLI (CLI-verified end-to-end by hand). Harness hardened (preflight,
wall-clock timeout, `stop_coding_task`, workspace guard). Web-UI path blocked upstream (#575);
GUI settings tab (#574) not yet built.

---

## The goal

Give Chad a **delegated coding worker** that runs **local models 24/7 with no cloud usage
limits**. It will never out-quality a frontier cloud model — that's not the bet. The bet is
a *tireless* worker: hand it a coding task, it grinds in a sandboxed workspace and reports
back; eventually it runs continuous browser-QA / bug-finding that cloud models can't do
cheaply. **Chad Code is a distinct surface from Chad-the-assistant** — Chad *dispatches* to
it asynchronously and never blocks its own turn on a coding job.

**Buy, don't build the harness.** OpenHands is the coding loop (the "hands"); Chad is the
**model-manager + launcher + task-framer + reporter** (the "brain"). We do not rebuild
edit/test/LSP loops.

### Model-specific tool calls and completion (2026-10-18)

`tools.openhands_native_tool_calling` remains the legacy Qwen baseline. The optional
`tools.openhands_native_tool_calling_by_model` map overrides it by exact LiteLLM model ID.
`openai/lfm2.5-24b-a2b` uses native mode: LM Studio returns structured `tool_calls` for
it. Forcing text mode produced mixed Hermes/LFM tool syntax and a final unexecuted call.
Use the actual endpoint response to choose mode; do not assume all local models need
the same parser.

The runner checks SDK execution status and the final agent event after `conv.run()`.
Error/stuck/paused runs, empty final messages, and final unexecuted tool requests exit
with `CHAD_DISPATCH_ERROR`, not `CHAD_DISPATCH_DONE`. These failures do not trigger the
MCP fallback, which could repeat side effects. A completed conversation still does not
prove a coding task is correct; outcome evals must independently inspect and test output.

The `fixbugs` probe captures its source/tests before dispatch, validates supplied fixtures,
and grades returned source in a fresh restricted Docker container against the protected tests.
Only `resolved` exits zero. No-op edits, modified tests, backend errors, poll timeouts,
and verifier failures have separate outcomes. Reports include verification metadata and
a sibling `.verification.log`. Other presets report completion without claiming PASS.

---

## Architecture

```
Chad (assistant, in-loop)
  └─ dispatch_coding_task   (approval-gated, async — returns a job handle, never blocks)
       1. ensure coder model resident in LM Studio   ← Chad's unique value-add
       2. docker run -d  <openhands image>  running chad_headless_runner.py
            → OpenHands SDK agent (terminal + file_editor tools) works in /workspace
            → local model via LM Studio (host.docker.internal:1234)
  └─ check_coding_task      (poll: status + transcript tail + workspace files)
```

Two execution paths exist in OpenHands; **Chad uses the first**:

| Path | What it is | Chad uses it? |
|---|---|---|
| **Headless SDK** | one detached `docker run` of `chad_headless_runner.py`, agent runs *in* the container against a mounted workspace | ✅ **Yes** — works, robust |
| App-server REST | the full OpenHands app (web UI + `/api/v1/app-conversations`) that spawns sibling sandbox containers | ❌ Not yet — blocked by a Windows startup race (below) |

The headless path has **no live web UI to steer a run** — it's fire-and-report. The UI path
is the future enhancement, gated on an upstream fix.

---

## What's built

- **`src/tools/dev/openhands_dispatch.py`**
  - `dispatch_coding_task(task, title?, workspace?, max_iterations?)` — ensures the coder
    model is resident, launches a detached container, returns `{job_id, container,
    workspace, model}`. Approval-gated, side-effecting, async.
  - `check_coding_task(job_id)` — `docker inspect` + `docker logs` + workspace file listing;
    reports `running | finished | failed`, exit code, transcript tail, files.
  - Both auto-discovered by `ToolRegistry` (no manual registration).
- **`docker/openhands/chad_headless_runner.py`** — the in-container SDK runner (mounted at
  `/chad`). Builds an `LLM` + `Agent(tools=[terminal, file_editor, task_tracker])` +
  `Conversation` and runs the task against `/workspace`. `native_tool_calling` and
  `task_tracker` are env-driven (surfaced as `tools.openhands_*` config, issue #576).
  **Capability upgrades (#569, all optional + graceful — a failure in any falls back to the
  base agent):**
  - **Skills** — `load_skills_from_dir(/chad/skills)` → `AgentContext(skills=...)`. Bundled
    AgentSkills live in `docker/openhands/skills/` (already inside the `/chad` mount):
    `code-review` (self-review/verify habit — the local-model answer to "no built-in success
    signal") and `github-workflow` (git/PR discipline). `CHAD_SKILLS_DIR` overrides the dir.
  - **Live event streaming** — `CHAD_STREAM_EVENTS` (default on) attaches a `Conversation`
    callback that prints a compact `CHAD_EVENT:` line per agent event, so a running job is
    watchable live via `docker logs` (issue #575 visibility) — no app-server needed.
  - **MCP + web search** — `CHAD_MCP_CONFIG` (JSON) → `Agent(mcp_config=...)`, assembled host-side
    by `_build_mcp_config()`. Defaults the coder ships with:
    - **Context7** (`tools.openhands_context7_mcp`, **on**) — hosted docs MCP (`resolve-library-id`,
      `query-docs`), keyless HTTP; current library/API docs when debugging.
    - **GitHub** (44 tools: issues/PRs/repo/code search) — **conditional, off by default**. 44 tool
      schemas on every job is heavy context most tasks don't need, so it's attached only when a
      dispatch opts in (`dispatch_coding_task(enable_github=True)`, which Chad sets for git/PR
      tasks) or the global `tools.openhands_github_mcp` flag is True. Authed via `Authorization:
      Bearer <PAT>` (an `api_key` field 401s). Guarded by the `github-workflow` skill.
    - **Web search** — the base image has no `npx`, so this is a `web-search` skill (not an MCP):
      the coder curls **Brave REST** (`BRAVE_API_KEY`) via the terminal. (Local SearXNG is
      intentionally not wired — low result quality.)
    - `tools.openhands_mcp_config` — raw `{"mcpServers": {...}}` passthrough for any other HTTP MCP.
    - **Secrets**: `_resolve_secret()` prefers a real value from `.env` when `secrets.yaml` holds a
      placeholder (get_secret is secrets-first, so a stale `your_..._here` stub would otherwise
      shadow the real `.env` token).
  - **codegraph MCP for large-repo exploration (issue #582)** — a local SQLite symbol-graph MCP
    (explore/callers/callees/impact/node/files/status), run as an **in-container stdio server**
    against the `/repo` reference. Unlike Context7/GitHub (hosted HTTP, no runtime needed),
    `codegraph` is an npm CLI, so it's baked into a custom image (`docker/openhands/
    Dockerfile.codegraph`, build: `docker build -f docker/openhands/Dockerfile.codegraph -t
    chad-code/openhands-codegraph:latest docker/openhands`). Gated on **both**
    `tools.openhands_codegraph_mcp` (default on) **and** a `reference` dir being mounted — ordinary
    build/fix jobs keep the lighter base image. Paired with the `large-repo-explore` skill.
    **Three non-obvious things this took to actually work (all learned live #582):**
    1. **The index must be built into a WRITABLE dir.** `codegraph serve` needs a `.codegraph/`
       index; a bare read-only `/repo` mount makes every call error *"not initialized"* and the
       model (correctly) falls back to `find`/`grep`. So the reference is mounted read-only at
       `/repo_src` and the entrypoint **copies it to a writable `/repo` and runs `codegraph init
       /repo`** before the agent starts (~5s per ~700 files; host repo untouched).
    2. **`codegraph_search` is filtered out** via `Agent(filter_tools_regex=...)` — its `kind`
       param collides with OpenHands' `kind` action discriminator (a `computed_field`) and
       hard-crashes the run. Symbol search is still available to the agent as the terminal CLI
       `codegraph query <name>` (same index). The other codegraph tools have no `kind` and work.
    3. **Graceful MCP fallback is mandatory** — an MCP schema failure surfaces at `conv.run()`,
       *past* `Agent()` construction, so the runner wraps the whole build-and-run and degrades to
       the base agent if the MCP path throws. Enabling an MCP must never be worse than not having it.

    **Proven live (n=1/arm, `AIAssistant-dev/src`, 503 files, qwen3.6-35b-a3b):** with the index
    built, the agent explored **entirely through codegraph** (status→files→explore×N, 0 terminal)
    and wrote a grounded report at **~150–180K input tokens vs 631K** for the no-codegraph control
    (which errored out with 0 files). The token win is real and attributable. Caveats: the model
    still (a) spends ~150s in a pre-write "think" stretch (recovered, but one earlier run timed out),
    and (b) makes minor path slips (a doubled `src/`, a guessed entry point). Adoption of
    `codegraph_explore` (the high-value tool) only happened once the brief was codegraph-first AND
    `codegraph_files` was called without an absolute-path filter — see the skill.
  - **Condenser + production critic (issue #584)** — both were already wired but config-only;
    #584 finished GUI exposure and added the missing production critic:
    - **Condenser** (`tools.openhands_use_condenser`, default off) — `LLMSummarizingCondenser`,
      summarizes old history once it exceeds `condenser_max_size` events so a long job can't die
      of context overflow. Unchanged logic, now toggleable from Settings → Chad Code.
    - **Critic** — the dev-only `ChadEvalCritic` (self-eval loop, issue #358) is unchanged. New:
      `tools.openhands_use_critic` (default off) attaches the SDK's built-in `EmptyPatchCritic`
      for **ordinary jobs** — flags (and gives one automatic retry to) a run that finishes
      claiming success but left no git diff.
    - **GUI toggles** — `ChadCodeTab.tsx` now exposes `use_condenser`, `use_critic`,
      `context7_mcp`, `github_mcp`, and `stream_events` (previously config-file-only).
    - **Still open**: live-probing `LLMSummarizingCondenser` against the explore-and-report eval
      tier (the one proven run long enough to plausibly trigger it) — needs a live Docker + LM
      Studio run, not done as part of #584.
- **`evals/chad_code_e2e_probe.py`** (#573) — escalating live reliability probe (Tier-1 game,
  Tier-2 full-stack todo). Drives the real tools, polls, diagnoses where a run broke down, and
  writes per-job reports to `evals/runs/chad_code/`.
- **`docker/openhands/`** — `docker-compose.yml` (app-server sidecar, the future UI path,
  with the `OH_WEB_URL` fix baked in), `.env.example`, and a README.

---

## Key design decisions

1. **Headless over app-server** — the app-server's sandbox orchestration has a Windows race
   (below) we can't fix without patching OpenHands. Headless is one container, no
   orchestration, and it works today. UI-steering is deferred, not abandoned.
2. **Async / fire-and-report** — dispatch returns a handle immediately; the container runs
   detached. Matches Chad's Brain/worker split and the EventBus/daemon patterns. Never
   blocks a turn on a multi-minute agent run.
3. **Chad owns model management** — `ensure_model_loaded` (LM Studio lifecycle) before
   dispatch. OpenHands cannot manage local VRAM on the box; Chad can. This is the real
   integration value, not the plumbing.
4. **Approval-gated + fresh-workspace-by-default** — a dispatch starts a container and edits
   files, so it's `requires_approval`. Each job gets its own empty workspace unless a path
   is supplied (no accidental writes into real repos).

---

## How to drive it (Docker + lessons the hard way)

**Prereqs:** Docker Desktop running; LM Studio serving on `:1234` with a coder model
downloaded. Recommended model: `qwen/qwen3.6-35b-a3b` (or `qwen3-coder-30b`).

**Run a task by hand (what the tool does):**
```bash
docker run -d --name chad-code-<id> \
  -v "C:/path/to/workspace:/workspace" \
  -v "C:/repo/docker/openhands:/chad:ro" \
  -e CHAD_TASK="Build ..." \
  -e CHAD_MODEL="openai/qwen/qwen3.6-35b-a3b" \
  -e CHAD_LLM_BASE_URL="http://host.docker.internal:1234/v1" \
  -e CHAD_LLM_API_KEY="lmstudio" \
  -e CHAD_MAX_ITER=30 \
  --add-host host.docker.internal:host-gateway \
  --entrypoint bash docker.openhands.dev/openhands/openhands:latest \
  -c "cd /workspace && python /chad/chad_headless_runner.py"
docker logs -f chad-code-<id>          # watch the transcript
```

**Lessons learned (all verified live 2026-07-20):**

- **The proven legacy Qwen setup uses `native_tool_calling=False`.** These deployments emit
  Hermes-style `<function=...>` **text**, not native OpenAI `tool_calls`, and LM Studio
  passes it through unparsed → tools never execute (the file never gets written). This is
  the exact failure that made OpenCode unreliable. With it `False`, OpenHands parses the
  text and tools fire.
- **Windows Docker bind mounts need `C:/...`, not `/mnt/c/...`.** A `/mnt/c` bind silently
  mounts an *empty* dir inside the WSL distro. (The app/compose still uses `/mnt/c` because
  it resolves the mount in-container, a different context.)
- **Two LM Studio URLs:** the **container** reaches it via `host.docker.internal:1234`;
  **Chad on the host** uses `localhost:1234` (for `ensure_model_loaded`).
- **LM Studio lists downloaded ≠ loaded.** `/v1/models` lists downloaded models on this
  version; `GET /api/v0/models` gives real `state` (`loaded`/`loading`/`not-loaded`). Preload
  a big model with `POST /api/v1/models/load {"model": "..."}` so the first agent call
  doesn't JIT-load-stall.
- **Old `python -m openhands.core.main` is broken** in this image (`No module named
  openhands.agenthub`). Use the `openhands.sdk` directly, as the runner does.

### Why the app-server / web-UI path is blocked (three Windows gotchas)

1. **MCP HTTPS-vs-HTTP** — `get_default_web_url()` forces `https://` when `WEB_HOST` is set,
   so the injected MCP client dials `https://…/mcp/mcp` at an HTTP server → SSL failure →
   500. **Fixed** with `OH_WEB_URL=http://host.docker.internal:<port>` (baked into compose).
2. **Windows reserved ports** — sandboxes publish ~4 random high host ports; Hyper-V/WSL
   reserves ranges (`netsh interface ipv4 show excludedportrange protocol=tcp`) → `docker
   start` fails. **Cleared** with `Restart-Service winnat -Force` (admin). Docker Desktop
   restart does *not* clear them; a reboot does.
3. **`exposed_urls` startup race — UNRESOLVED (upstream OpenHands bug).** The app reads
   `sandbox.exposed_urls` on a `SandboxInfo` captured *before* the container reaches
   `RUNNING` → empty-message `AssertionError`. The sandbox is healthy seconds later, so it's
   a stale-read timing bug, not networking. Config workarounds (`SANDBOX_STARTUP_GRACE_SECONDS`,
   `ADD_TO_ANY` grouping) were insufficient. Needs a newer/non-`saas` image or an upstream
   patch. This is why Chad uses the headless path instead.

---

## Proven end-to-end (eval runs, 2026-07-21)

Driven by `evals/chad_code_e2e_probe.py` (issue #573) against live Docker + LM Studio with the
default `qwen3.6-35b-a3b`:

- **Tier 1 — single-file game (Snake):** PASS in ~246s. Real, complete game; the agent
  self-verified structure with `grep` before finishing. (Nit: its `requestAnimationFrame` loop
  has no frame throttle → unplayably fast. Functionally correct, misses the *unstated* "make it
  playable" — a task-framing lesson, not a harness bug.)
- **Tier 2 — full-stack TODO app (multi-file):** PASS in ~306s. Produced `server.py` (stdlib
  HTTP JSON API + `todos.json` persistence), `index.html`, `test_server.py`, `README.md`. The
  agent's own test **failed (exit 1), it self-corrected, and re-ran to green** — a real
  multi-file + self-correcting test loop. **Independently re-verified**: all 4 unittests pass.

- **Tier 3 — HARD: multi-module CLI app (expense tracker):** PASS in ~335s. 5 modules
  (`storage`/`tracker`/`cli` + a 9-test suite + README), argparse subcommands (add/list/report/
  export-CSV), amount/date/category validation. **Independently verified beyond the agent's own
  unit tests**: ran the CLI end-to-end — add→persist, list, `report` (per-category totals + grand
  total math correct), `export` (valid CSV), and invalid-amount rejection all work. The agent
  self-verified via unit tests only (never ran the CLI), yet the CLI worked when driven by hand.

- **Edit-existing-code — fix a seeded buggy library:** PASS in ~155s. The workspace was seeded
  with a deliberately buggy `mathlib.py` (5 planted bugs) + a *correct* failing `test_mathlib.py`;
  the task was to make the tests pass by fixing ONLY the source. **Independently verified**: all 7
  tests pass, `test_mathlib.py` is byte-identical to the seed (it did NOT cheat by editing tests),
  and the fix is surgical (40 lines, each bug fixed in place — factorial off-by-one, is_prime<2,
  divide `ValueError`, gcd return, fib off-by-one). So read-diagnose-surgically-edit works too.

So the bare `terminal` + `file_editor` toolset already handles multi-file, a verify loop, AND
surgical edits to existing code — it did *not* lose the plot in any tier. `task_tracker` (below)
is added for larger tasks, not because these needed it.

- **Explore-and-report — real 405M codebase (odysseus, ~6,255 files), read-only `/repo` mount:**
  PASS in ~1155s (~19min), 412K input tokens — **3.5x longer and ~4-5x more tokens than any
  build tier**, despite writing only one file. **Independently verified**: `/repo` left
  untouched (`git status` clean), and 5/5 spot-checked cited file paths in the report are real
  (not hallucinated). The report itself is genuinely specific — real architecture, real
  standout modules with real paths, concrete "adapt this, here's why" recommendations, not
  generic filler.
  **What this revealed:** cost scales with codebase size, not task complexity — raw
  `find`/`grep`/`cat` exploration burns context fast with no repo-level retrieval. This is the
  harness's real ceiling: large-codebase work is expensive as-is; a real second-brain/audit
  workflow at this scale would want RAG/embeddings over the repo rather than context-stuffing.

**Still unproven (where to probe next):** the common thread of all four passing evals is a
**built-in verification signal** (a test suite, or a runnable CLI). The real open questions are
tasks *without* one — where the agent can't run something to know it succeeded (the Tier-1 Snake
"unplayably fast" nit is a mini case: it passed every checkable requirement and still wasn't
playable). Also unproven: scope large enough to outgrow the context window (condenser territory);
under-specified tasks needing a clarifying question (headless can't ask — it guesses or stalls);
and non-stdlib / network dependencies.

## Model-load safety (concurrent loads / VRAM)

Chad's `_ensure_coder_model` calls `ensure_model_loaded`, which is a thin wrapper over
`model_lifecycle.ensure_resident` — **the single serialized, VRAM-budgeted coordinator every
load/unload goes through.** So a dispatch's coder-model load can't race Chad's main-model load
into the duplicate-JIT-load OOM that `model_lifecycle` exists to prevent; it queues behind the
same lock and respects the VRAM budget. The coder model is loaded as `owner="warm"` (stays
resident, never auto-released) — fine for fire-and-forget dispatch (the detached container needs
it after the dispatch call returns), but note it does pin VRAM until another load evicts it.

## Current limitations

- **No live UI** to watch/steer a run (headless is fire-and-report). `tool-ui.tsx` chat-bubble
  entries for the two tools ARE in place (merged via #577); the richer **job panel** UX (#574)
  is not built.
- **Tier 3 untested** — multi-step "Chad tasks" (e.g. pull context from email/Drive → seed a
  detailed brief → build) not yet run.

---

## Next steps

See the epic ([#569](https://github.com/MatthewLoyed/AIAssistant/issues/569)) for the tracked
sub-issues:

1. **Evals / full-E2E stress tests** — real multi-file tasks (e.g. "build a small Flask API
   with a passing test"), a repeatable Chad Code eval probe, and reliability/pass-rate
   measurement across models (35B vs 30B vs others) and `max_iterations`.
2. **UI integration — make dispatch/check braindead-easy** — a first-class surface (a
   "delegate to Chad Code" affordance + a job panel showing status/transcript/files),
   including the `tool-ui.tsx` entries for the two tools.
3. **Unblock the web-UI / app-server path** — a newer/non-`saas` OpenHands image or upstream
   patch for the `exposed_urls` race, to restore live steering.
4. **Housekeeping** — job-container lifecycle/pruning; surface model + `native_tool_calling`
   as config; confirm the `qwen3.6-35b-a3b` vs `qwen3-coder-30b` default.
