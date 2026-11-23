# Browser named-SaaS cluster — 2026-07-26

Persistent regression suite: `evals/datasets/browser_named_saas.jsonl`  
Re-run:

```powershell
$env:PYTHONPATH="."; $env:CHAD_SERVER_URL="ws://localhost:8788/ws"
$env:CHAD_MAIN_ROOT="C:\Users\ExampleUser\Documents\Coding\Fun\AIAssistant"
.\.venv\Scripts\python.exe -m evals.runner --suite browser_named_saas --live --workflow --judge --judge-model "google/gemma-4-26b-a4b-qat"
```

Catalog: [`FOUND_AND_FIXED.md`](FOUND_AND_FIXED.md) (list-preview FIXED; product-name gate shipped; login hard-stop + ChatGPT routing still OPEN).

## Baseline (`run_20260726_215446`)

| Case | Result | Tools |
|---|---|---|
| `knot_vendor_terse` | **PASS** | browser → conversation_detail |
| `outlook_angela_baker` | **FAIL** | `search_gmail` / `read_gmail` only |
| `chatgpt_prompt4` | **FAIL** | `web_search` only |

Transcripts (local, often gitignored): `evals/runs/transcripts/run_20260726_215446/`

## After product-name gate (~22:23–22:41)

| Case | Result | Notes |
|---|---|---|
| Outlook | Killed ~14m | Browser did open; unsigned-in MS surface; looped instead of `LOGIN REQUIRED` |
| ChatGPT | **FAIL** `run_20260726_223745` | Still `web_search` only; invented “Prompt 4” |

## Failure clusters / lessons

1. **Named product → browser** — ACTIVITY CONTEXT / “close” API must not override an explicit app name (Outlook≠Gmail; ChatGPT chat≠public web). Gate copy shipped; ChatGPT phrasing still escapes it.
2. **Login walls must hard-stop** — persona + `[PAGE SURFACE: login]` are not enough if URL heuristics miss `login.microsoftonline.com` / marketing splash; model loops on huge truncated snapshots.
3. **Eval connect ≠ GUI Auto** — `platform=eval` skips classify, but WS preload used `ui_mode=auto` and JIT-loaded gemma-1b. Fixed: `client_type=eval` + connect preload `agent`/`plan`.
4. **ChadBrowser sandbox** — prefer Playwright MCP `--sandbox` on desktop; `--no-sandbox` only for Docker/root/CI (yellow Chrome infobar).
5. **Judge VOID ≠ model fail** — empty/unparseable judge output needs `submit_verdict` / rejudge before blaming the agent.

## OPEN (re-test targets)

- [ ] `outlook_angela_baker` — signed-in Outlook thread summary via browser; no Gmail substitute; clean exit on login wall
- [ ] `chatgpt_prompt4` — open chatgpt.com chat “ai agent test prompt…”, report prompt 4 from that chat
- [ ] Knot chronology/attribution — latest *vendor* turn, not user line / truncated preview (soft residual after list→detail PASS)
