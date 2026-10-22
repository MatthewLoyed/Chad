# AGENTS.md — evals/

**Single source of truth for agents** running or writing harness evals. Deep schema/rubrics:
[`REFERENCE.md`](./REFERENCE.md). Lessons log: [`HARNESS_LESSONS.md`](./HARNESS_LESSONS.md).
Cursor entry: [`.claude/skills/eval-harness/SKILL.md`](../.claude/skills/eval-harness/SKILL.md).

Use when asked to test the harness, run evals, check tool/subagent selection, find prompt
contradictions/bloat, verify prompt/tool-selection/orchestration changes, or HITL quality suites.

**Diagnosing a live/workflow failure?** Read
[`docs/evals/FOUND_AND_FIXED.md`](../docs/evals/FOUND_AND_FIXED.md) and
[`HARNESS_LESSONS.md`](./HARNESS_LESSONS.md) before inventing a root cause.

## Matt defaults

When Matt asks to “run evals” without naming a suite:

1. **Worktree Brain for interactive loops** — start `server.py` from *this* worktree on `:8788`
   (or another free port), set `CHAD_SERVER_URL=ws://localhost:<port>/ws`. Prod `:8767` cannot see
   uncommitted work. **Never kill/restart/spawn on `:8767`.** Only use `:8767` if Matt explicitly
   asks for a prod-behavior check (connect-only).
2. **Do not run `tool_selection`** (or dry-run tool-binding suites) unless asked — treated as solved.
3. **Priority:** `--workflow` / custom gitignored HITL suites (`wedding_*`, dinner_*, ad-hoc) →
   other live/workflow if still useful.
4. **Artifacts are per-worktree** (mostly gitignored). Search sibling worktrees for prior
   `evals/runs/run_*.json` and `evals/datasets/wedding_*.jsonl` before concluding “no prior results.”

Eval residency (also see `eval_exclusivity.py`):

- **Eval owns the box** while any eval turn is in flight (desktop/cron wait).
- **Interactive** model loads (no background queue wait behind a stale interactive touch).
- **Keep the 9B selector hot** across cases (no unload-after-select thrash).
- **Live/workflow stay sequential** — subagents + VRAM; do not parallelize live cases.
- **Dry-run tool-selection may use ~6 workers** (SpyAdapter, no main-model generate).
- **Connect preload is agent (or plan), never GUI auto** — eval auth sends `client_type=eval`
  + `ui_mode=agent` so `_maybe_preload_on_connect` does not JIT-load intent-router gemma-1b.
  Use `open_eval_ws(ui_mode="plan")` for planner-heavy suites.
  Persistent named-SaaS regression: `--suite browser_named_saas` (see
  `docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md`).

## Ports

| Port | Role |
|---|---|
| `:8788` (typical) | Worktree / dev Brain — **default for edit → re-eval** |
| `:8767` | Production Brain — **connect only**; never start/stop/kill |

## Worktree Brain bootstrap (before live/workflow)

`$main` = `C:\Users\ExampleUser\Documents\Coding\Fun\AIAssistant`. Worktrees often lack `.venv` and
gitignored secrets — borrow from `$main`.

### 1. Docker + Chad sidecars

```powershell
docker info | Out-Null
docker compose -f "$main\docker\chad-services.yml" up -d
```

### 2. Copy cwd-resolved config into the worktree

`CHAD_MAIN_ROOT` does **not** supply `config/token.json` / prefs — those resolve from **cwd**.

```powershell
$roc = (Get-Location).Path
foreach ($f in @('preferences.yaml','token.json','credentials.json')) {
  $src = Join-Path $main "config\$f"
  $dst = Join-Path $roc "config\$f"
  if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst -Force }
}
```

### 3. Eval client env (auth handshake)

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:CHAD_MAIN_ROOT = $main
$env:CHAD_SERVER_URL = "ws://localhost:8788/ws"
Get-Content "$main\.env" | ForEach-Object {
  if ($_ -match '^\s*CHAD_REMOTE_TOKEN\s*=\s*(.+)$') {
    $env:CHAD_REMOTE_TOKEN = $Matches[1].Trim().Trim('"').Trim("'")
  }
  if ($_ -match '^\s*CHAD_SERVER_TOKEN\s*=\s*(.+)$') {
    $env:CHAD_SERVER_TOKEN = $Matches[1].Trim().Trim('"').Trim("'")
  }
}
```

### 4. Start worktree Brain

```powershell
$env:CHAD_DAEMONS_ENABLED = "false"
$env:PORT = "8788"
& "$main\.venv\Scripts\python.exe" src\remote\server.py
# health: Invoke-WebRequest http://localhost:8788/health -UseBasicParsing
```

### 5. Preflight (must exit 0)

```powershell
& "$main\.venv\Scripts\python.exe" -m evals.preflight --suite <suite> --live --judge-model "google/gemma-4-26b-a4b-qat"
```

| Check | Typical fix |
|---|---|
| `auth_token` | Load tokens into **eval client** env (step 3) |
| `google_oauth` | Copy `token.json` / re-auth in main |
| `server` | Start worktree Brain; fix `CHAD_SERVER_URL` |
| `judge_model` | Pin an **already-loaded** id — never JIT a heavy model |

## Eval mode (`platform="eval"`)

Harness turns use a different residency policy than desktop chat
(`src/core/orchestration/eval_exclusivity.py`). Do not “fix” evals by making them behave like
`gui` turns. Details: `docs/features/MODEL_LIFECYCLE.md`.

## Lanes (commands)

```powershell
$main = "C:\Users\ExampleUser\Documents\Coding\Fun\AIAssistant"
$env:PYTHONPATH = (Get-Location).Path
$env:CHAD_MAIN_ROOT = $main
$env:CHAD_SERVER_URL = "ws://localhost:8788/ws"
$py = "$main\.venv\Scripts\python.exe"

# Free / CI-style
& $py -m evals.runner --prompt-audit
& $py -m evals.runner --tool-contract
& $py -m evals.runner --replay
& $py -m evals.runner --memory-layer

# Primary for Matt: workflow / HITL on worktree Brain
& $py -m evals.runner --suite workflows --workflow --judge --judge-model "qwen/qwen3.5-9b"
& $py -m evals.runner --suite wedding_dinner_plan --live --judge --judge-model "google/gemma-4-26b-a4b-qat"

# tool_selection — ONLY if Matt asks
# & $py -m evals.runner --suite tool_selection --judge --judge-model "qwen/qwen3.5-9b"

& $py -m evals.runner --list
& $py -m evals.runner --scoreboard
& $py -m evals.runner --coverage
```

| Want | Lane | Server |
|---|---|---|
| Full plan→approve→execute | `--workflow` | worktree Brain |
| HITL / quality | `--live` or `--workflow` + Gemma judge | worktree Brain |
| One real turn | `--live` | worktree Brain |
| Prompt contradictions / bloat | `--prompt-audit` | no |
| Tool bind (only if asked) | dry-run | no |

Dry-run is **not** faithful for web/MCP tool selection — assert those live.

**Judge models:** pin a small **already-loaded** id (`qwen/qwen3.5-9b`). HITL quality:
`google/gemma-4-26b-a4b-qat`. Never JIT `qwen/qwen3.6-35b-a3b` as judge. Check:
`(irm http://localhost:1234/v1/models).data.id`. Scores are **0–10** (see `REFERENCE.md`).

Offline rejudge (no Brain turn): `python -m evals.tools.rejudge_run --run <ts> --suite <name> ...`

## Reading results

- `evals/runs/latest.md` — failures first
- `evals/runs/triage.md` — clustered root causes
- `evals/runs/transcripts/run_<ts>/` — full trajectories
- Outcomes: pass / fail / `void_infra` / `void_harness` — voids **excluded** from pass rate.
  `trustworthy: false` → fix infra and re-run; do not compare rates.

Append harness-class findings to [`HARNESS_LESSONS.md`](./HARNESS_LESSONS.md).
LLM-as-judge **analyzes**; Chad-on-Chad (#358) **fixes and retests** —
[`CHAD_SELF_EVAL_LOOP.md`](../docs/architecture/CHAD_SELF_EVAL_LOOP.md).

## Writing a case

Append one JSON line to `evals/datasets/*.jsonl`. Only `id`/`prompt` required. Unknown
`expect`/`judge` keys fail at load. Full schema: [`REFERENCE.md`](./REFERENCE.md) → Datasets.

- `category: "planning"` → plan lane (Scout → Planner)
- Live keys: `min_steps`/`max_steps`, `response_includes`, `tool_args`, `requires_approval`, …
- Workflow keys: `must_complete`, `min_rounds`/`max_rounds`, …

HITL design: [`docs/evals/QUALITY_HITL.md`](../docs/evals/QUALITY_HITL.md).

## Ad hoc probes

```python
import asyncio, os
os.environ.setdefault("CHAD_SERVER_URL", "ws://localhost:8788/ws")
from evals.live import probe_live
trace = asyncio.run(probe_live("your prompt", category="planning"))
```

Interactive clarifying questions: `evals/interactive_workflow.py` (see `REFERENCE.md`).

Browser: `evals/browser_playwright_probe.py`, `evals/browser_fara_probe.py`.
Coder bench: `python -m evals.coder_bench`. Session isolation: `python -m evals.session_isolation_probe`.

## Common mistakes

- Skipping bootstrap → VOID/preflight failures that look like model bugs
- Pointing live/workflow at `:8767` to test uncommitted code
- Heavy / unloaded judge model (JIT + VRAM thrash)
- Forgetting `category: "planning"` when testing Scout/Planner
- Treating a 1-step re-gather plan as a pass after Scout already read the doc
- Treating judge **suggestions** as the harness fix — classify → `#358` / lessons log
