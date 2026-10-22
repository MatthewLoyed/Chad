# Fara-7B screenshot-native spike (issue #288) — final findings

**Date:** 2026-07-03  
**Model:** `microsoft_fara-7b` (Fara-7B Q6 GGUF in LM Studio, context ≥15k, temp 0)  
**Harnesses:** `evals/browser_fara_probe.py` (in-repo thin loop) and official `fara-cli` (`microsoft/fara`) against the same LM Studio endpoint

## Verdict (read this first)

**Neither the local CUA (Fara-7B Q6) nor our harness is good enough for reliable web automation yet.**

Early Honda Civic success and WebVoyager’s ~73% headline overstated readiness. On harder tasks (multi-filter shopping, logged-in dashboards), both the in-repo probe and **Microsoft’s own `fara-cli`** fail in practice: dropdown thrash, wrong-site recovery, and — critically — **confident hallucinated final answers** when the model never opened a listing.

**Decision:** Do **not** promote Fara as Path B for production `browser_agent`. Spike is complete as a learning exercise.

**Update (same day):** A **DOM + tools + honest feedback** loop with **Gemma-4-26B** (`evals/browser_hybrid_probe.py`) **did** prove local multi-step web capability (HN, AutoTrader.ca, GTA VI names, ExampleBiz owners). The gap was harness feedback/refs, not “local models can never browse.” See `docs/features/BROWSER_HYBRID_PROBE.md`.

## Success criteria (from #288) — revised

| # | Criterion | Result |
|---|---|---|
| 1 | Thin loop end-to-end: screenshot → Fara → action → screenshot… | **Pass** (mechanics work) |
| 2 | AutoTrader: simple Honda Civic price | **Pass** (early run, 7 turns) — easy trajectory |
| 2b | AutoTrader: Mazda 3, year ≥2019, under $20k, **return listing URL** | **Fail** (thin probe + official CLI) |
| 2c | Supabase quota golden | **Fail / env-sensitive** — session only applies on `/dashboard`; marketing/`/login` look signed-out |
| 3 | Compare to DOM baseline | Both paths inadequate for multi-step UI with small local models |
| 4 | Decision: primary / hybrid / abandon | **Do not promote.** Park vision CUA for production; research hybrid approaches |

## What we proved

### Mechanics (good)

- Screenshot → Fara → Playwright mouse/keyboard/goto works on ChadBrowser (`src/shared/chad_browser_profile.py`).
- Official-style loop matters: **multi-turn chat history**, **last 3 screenshots**, **official system prompt**, Magentic observation phrasing (`I typed…`, page-up/page-down scroll). Over-constrained policy (block all `visit_url`, long harness essays) made the model *worse* (scroll thrash).
- Q6 GGUF is usable for the loop; LM Studio reports it as near Q8 for this model. Quantization is **not** the primary failure mode vs official CLI on the same endpoint.

### Capability (not good enough)

| Run | Outcome |
|---|---|
| Thin probe, Mazda 3 + filters + URL | Reached a real US listing (`…/vehicle/783017903`) then `history_back`; hit max turns **without** reporting price/URL |
| Official `fara-cli`, same task | Abandoned US site for **autotrader.ca**, never opened a listing, `terminate success` with a **fabricated** year/trim/price/URL in thought text |
| Supabase quota | Invented hosts (`dash.supabase.io`), early LOGIN REQUIRED on marketing pages; cookies apply on `/dashboard`, not `/` or `/login` |

Official Fara uses **pre-`tool_call` thought text as the final answer**. A confident hallucination therefore looks like a pass. Any production path must **ground terminate** (e.g. listing URL must match `page.url`; numbers must have been memorized from a prior observation).

### Benchmark context (why 73% misled us)

| Benchmark | Fara-7B (paper) |
|---|---|
| WebVoyager | **73.5%** (headline) |
| Online-Mind2Web | **34.1%** |
| DeepShop | **26.2%** |
| WebTailBench | **38.4%** |

Multi-filter shopping and logged-in SaaS sit closer to the **25–40%** band. Paper numbers use **vLLM + full/half precision** and controlled browsers — not a guarantee for live AutoTrader/Supabase in LM Studio.

## Harness notes (keep for later)

- Prefer **thin** harness: official system prompt + task text; execute actions; feed observations. Policy in code only where safety requires it (no credential typing — #279).
- Enter authenticated apps at the **app URL** (e.g. `https://supabase.com/dashboard`), never marketing `/` or `/login` (login shells look signed-out even when cookies exist).
- ChadBrowser profile seeding: `scripts/maintenance/seed_chrome_profile.ps1` (no-downgrade; backup first).
- Probe: `evals/browser_fara_probe.py` (rewritten to mirror `microsoft/fara` `FaraAgent` message loop).

## Direction after this spike

Fara-7B as a vision-only CUA is **not** the path. Same-day follow-up proved **Gemma-4-26B + DOM tools + honest feedback** (`evals/browser_hybrid_probe.py`) *is* capable — see `docs/features/BROWSER_HYBRID_PROBE.md`. The missing piece was harness feedback/refs, not “local models can never browse.”

Next: full dataset eval, safety/approval for writes (#279), then wire into `browser_agent`. Do not ship Fara as Path B; desktop (#278) should follow hybrid/DOM+feedback lessons.

## How to re-run (for future experiments)

```powershell
$env:PYTHONPATH="."
# Thin in-repo probe (ChadBrowser)
& <venv>\python.exe evals\browser_fara_probe.py --task autotrader
& <venv>\python.exe evals\browser_fara_probe.py --task supabase

# Official CLI baseline (Firefox; install: pip install -e .tmp/fara; playwright install firefox)
& <venv>\python.exe -m fara.run_fara --task "..." --base_url http://localhost:1234/v1 `
  --api_key lm-studio --model microsoft_fara-7b --headful --max_rounds 30 --start_page about:blank
```

Requires LM Studio serving `microsoft_fara-7b` (temp 0, context ≥15k). Never point Playwright at real Chrome User Data.
