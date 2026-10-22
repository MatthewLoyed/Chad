# OpenHands — Chad Code's coding harness

OpenHands is the external coding harness for **Chad Code** (epic #569): it drives a
**local model** (LM Studio by default) to do real coding work in a workspace, 24/7, with no
cloud usage limits. Chad's role is model-manager + launcher + task-framer + reporter;
OpenHands is the hands.

## How it actually works today — headless (the path Chad's tools use)

`dispatch_coding_task` (`src/tools/dev/openhands_dispatch.py`) runs a **single detached
`docker run`** of the OpenHands image executing `chad_headless_runner.py` (this dir,
mounted at `/chad`) against a mounted `/workspace`. No app-server, no sandbox spawning —
which sidesteps the Windows `exposed_urls` startup race (see below). Proven end-to-end
2026-07-20: local `qwen3.6-35b-a3b` built + verified a file via the tool chain.

Nothing to start up-front — Chad launches the container per job. Requirements:
- Docker Desktop running; LM Studio serving on `:1234` with the coder model available.
- `check_coding_task(job_id=...)` reports status + transcript + workspace files.

Config (all optional, `config.yaml` → `tools.*`): `openhands_image`,
`openhands_coder_model` (default `openai/qwen/qwen3.6-35b-a3b`), `openhands_max_iterations`,
`chad_code_workspace_root`, `openhands_codegraph_mcp` (default on), `openhands_codegraph_image`
(default `chad-code/openhands-codegraph:latest`, see below).

### Large-repo exploration: the codegraph MCP (issue #582)

When a dispatch mounts a `reference` repo read-only at `/repo` (explore-and-report tasks), Chad
attaches a local **codegraph** MCP — a SQLite symbol graph (search/explore/callers/callees/impact)
run *inside* the container as a stdio server: `codegraph serve --mcp --path /repo --no-watch`. This
needs Node + the `@colbymchenry/codegraph` npm CLI, which the base image doesn't have, so it's
baked into a small custom layer:

```powershell
docker build -f docker/openhands/Dockerfile.codegraph -t chad-code/openhands-codegraph:latest docker/openhands
docker run --rm --entrypoint bash chad-code/openhands-codegraph:latest -c "codegraph --version"  # -> 0.9.9
```

This image is only used for reference-mounted jobs (gated on `tools.openhands_codegraph_mcp`,
default on, AND a `reference` dir being present) — plain build/fix jobs keep the lighter base
image. Paired with the `large-repo-explore` skill (prefer `codegraph_*` tools over raw
`find`/`grep`/`cat`). This targets the explore-and-report token ceiling measured in
`CHAD_CODE_RESEARCH.md` §1 (412K tokens / ~19min on a 6,255-file repo).

**The critical local-model unlock:** the runner sets `native_tool_calling=False` because
mid-size local coder models emit Hermes-style `<function=...>` **text**, not native OpenAI
`tool_calls` (LM Studio passes it through unparsed) — without this, tools never execute.
This is the same failure mode that made OpenCode unreliable with these models.

## The app-server / web-UI path (compose below) — currently blocked

The `docker-compose.yml` here runs the full OpenHands **app** (web UI + REST API on `:3001`)
for an interactive, steerable session. It's **not** what the tools use today because of
Windows gotcha #3 below (`exposed_urls` race). Kept for when that's fixed upstream.

```powershell
copy docker\openhands\.env.example docker\openhands\.env
# edit .env — set the two /mnt/c/... paths
docker compose -f docker/openhands/docker-compose.yml up -d
curl http://127.0.0.1:3001/          # 200 = healthy
```

Stop: `docker compose -f docker/openhands/docker-compose.yml down`

## Three Windows gotchas (root-caused 2026-07-20, see #447) — #1 solved here, #2 host-level, #3 unresolved

### 1. The `OH_WEB_URL` fix (baked into the compose file)

OpenHands' legacy `get_default_web_url()` (`app_server/config.py`) assumes **HTTPS**
whenever `WEB_HOST` is set:

```python
def get_default_web_url():
    """If present, we assume we are running under https."""
    return f'https://{web_host}'
```

So the agent-server's auto-injected MCP client dials
`https://host.docker.internal:3001/mcp/mcp`, but the app serves plain **HTTP** →
`httpx.ConnectError: [SSL] record layer failure` → unhandled 500 → the conversation
start-task flips to `ERROR` before the coding loop ever runs. It looks like "the model
can't code" but it's pure wiring.

**Fix:** `OH_WEB_URL=http://host.docker.internal:${OH_PORT}` (parsed via
`from_env(AppServerConfig, 'OH')`), which overrides the scheme for both the injected MCP
endpoint and the event webhooks. Already set in `docker-compose.yml`.

### 2. Windows reserved-port collisions (host-level, not fixed by this file)

Each sandbox publishes ~4 **random** high host ports. Windows (Hyper-V/WSL) reserves
several TCP ranges (`netsh interface ipv4 show excludedportrange protocol=tcp`, e.g.
`52417–52516`, `52617–52916`, `50000–50059`). When a sandbox port lands in one,
`docker start` fails with *"an attempt was made to access a socket in a way forbidden by
its access permissions."*

If sandboxes fail to start intermittently, clear the reservations from an **admin** shell
with `net stop winnat; net start winnat` (or `Restart-Service winnat -Force`). A Docker
Desktop restart alone does **not** clear them; a reboot does. Verified live 2026-07-20:
after the winnat reset the explicit `docker start` port-bind failures stop.

### 3. `exposed_urls` startup race — UNRESOLVED (upstream OpenHands bug)

Even with #1 and #2 cleared, the start-task can flip to `ERROR` with an **empty `detail`**.
Root cause (traced in `app_server/.../live_status_app_conversation_service.py`): the app
calls `_get_agent_server_url(sandbox)` on a `SandboxInfo` captured *before* the container
reaches `RUNNING`, so `sandbox.exposed_urls is None` → `AssertionError` (empty message) →
`Error starting conversation`. The sandbox itself comes up healthy moments later (its ports
publish and `/health` returns 200 from both host and the app container), so this is a
stale-read timing bug in the app, not networking, the model, or the two fixes above.

Config-level workarounds tried and found **insufficient** (2026-07-20): the
`SANDBOX_STARTUP_GRACE_SECONDS` knob (set here, harmless, kept) fires too late; the
`ADD_TO_ANY` sandbox-grouping strategy can't reuse the warm sandbox because sandbox
*discovery* hits the same assert. Likely needs a newer/non-`saas` OpenHands image or an
upstream patch. Tracked in #447 / #569.

## Port note

The app is published on `0.0.0.0:${OH_PORT}` (not `127.0.0.1`) because the sibling
agent-server sandbox containers must reach it via `host.docker.internal:${OH_PORT}` — a
loopback-only bind is unreachable from them. Firewall the port off your LAN if needed.
Port `3001` (not `3000`) avoids the `chad-excalidraw-canvas` sidecar on `:3000`.

## API quick reference (app 1.6.0 / agent-server 1.17.0)

| Action | Call |
|---|---|
| Create + run a task | `POST /api/v1/app-conversations` with `{initial_message: {role:"user", content:[{type:"text", text:"..."}], run:true}, title, llm_model?}` → returns async **start-task** `{id, status}` |
| Poll start-task | `GET /api/v1/app-conversations/start-tasks?ids=<id>` → `status`: `WORKING → … → READY \| ERROR`; on READY yields `app_conversation_id` |
| Conversation status | `GET /api/v1/app-conversations?ids=<id>` → `execution_status` (`idle/running/finished/error/…`), `conversation_url` |
| Read events (results) | `GET /api/v1/conversation/<id>/events?id=<cursor>` |
# Local generation request timeout

`tools.openhands_llm_timeout_s` controls each LLM request (default 900 seconds),
passed to the runner as `CHAD_LLM_TIMEOUT_S`. This is separate from the overall
job watchdog (`openhands_job_timeout_s`). The SDK's 300-second default cancelled
a Qwen test-file generation at roughly 12 tokens/second and retried it without
producing a file. Keep jobs bounded, but allow enough time for a single local
generation; prefer compact fixtures and incremental edits.
