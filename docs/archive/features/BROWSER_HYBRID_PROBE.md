# Browser hybrid probe — Gemma + DOM + honest feedback

**Date:** 2026-07-03  
**Probe:** `evals/browser_hybrid_probe.py`  
**Model:** `google/gemma-4-26b-a4b-qat` (LM Studio)  
**Profile:** ChadBrowser (`src/shared/chad_browser_profile.py`)

## Verdict

**Gemma-class local models are capable of real multi-step web work** when the harness gives stable control identity and truthful, specific feedback. The breakthrough was **not** a new model family — it was fixing observation and feedback after Fara-7B (vision-only CUA) proved insufficient (#288).

Fara remains a useful experiment (see `docs/features/FARA_SPIKE.md`) but is **not** the path forward. The hybrid DOM+tools loop is.

## Biggest win: feedback

Early failures looked like “model can’t browse.” Logs showed the model was often **doing the right thing against a lying harness**:

| Harness bug | Model effect |
|---|---|
| Refs renumbered every turn (`e39` = Make, then Postal code) | Typed postal code into “Make” by accident |
| Click result `Clicked e3` with no label | Could not learn what was clicked |
| Scroll no-op labeled `OK` | Kept scrolling |
| Same FAIL click allowed forever | Spam `e33` / dead controls |
| Parse error aborted the run | One prose turn killed a good trajectory |
| Detail pages: interactive-only snapshot | No prices/names → never terminate |

**Fixes that unlocked Gemma:**

1. **Stable session refs** — fingerprint → `eN`, stamped as `data-chad-ref` (same control keeps the same id).
2. **Honest feedback** — `OK` / `FAIL` / `NO_EFFECT` / `BLOCKED` with role, name, URL change, control value after type, scroll position.
3. **Hard loop-breaker** — dead-end action signatures are **BANNED** (prompt alone was not enough).
4. **READABLE TEXT** — headings, meta, body lines, prices, name-like phrases (not only buttons).
5. **Parse continue** — invalid JSON → feedback, do not abort.
6. **On-demand `screenshot`** — expensive; use when scroll is NO_EFFECT, text is cookie-thin, or stuck after failures; image + notes delivered next turn (max 3/run).
7. **`select`** for native `<select>` dropdowns (`options=[...]` in CONTROLS).

**Lesson:** Prefer a truthful, simple harness over a clever action space. Soft “prefer FEEDBACK” rules do not stop loops; **refuse the repeat**.

## Passed tests (2026-07-03)

| Task | Result | Notes |
|---|---|---|
| Hacker News #1 title + points | **Pass** (~31s, 2 turns) | Title matched live HN |
| AutoTrader.ca — Mazda search, open listing, year/model/price | **Pass** (~30s, 4 turns) | After stable refs + feedback; use `.ca` (US `.com` often unavailable here) |
| Rockstar GTA VI — character names on-site only | **Pass** (~27s, 4 turns) | `NAMES=Jason, Lucia` (correct protagonists) |
| example-smallbiz.com — owners on About | **Pass** (~13s, 3 turns) | `OWNERS=Jordan Sample, Alex Example` (verified correct) |

Curated proof trajectories (tracked): `evals/runs/proof/` — see that folder’s README.
Other run artifacts under `evals/runs/` stay gitignored.

## Partial / not yet reliable

| Task | Result | Notes |
|---|---|---|
| ExampleBiz early-access form (name, email, city, Club membership, submit) | **Partial** | Name/email/city filled with confirmed values; custom membership widget not in CONTROLS; vision turn often yields prose not JSON; submit/confirm not completed in 30 turns |

US `autotrader.com` “unavailable” pages are an **environment** issue — not a model fail. Prefer `autotrader.ca` for local probes.

## How to run

```powershell
$env:PYTHONPATH="."
$env:PYTHONIOENCODING="utf-8"
& <venv>\python.exe evals\browser_hybrid_probe.py --task hn
& <venv>\python.exe evals\browser_hybrid_probe.py --task autotrader_simple
& <venv>\python.exe evals\browser_hybrid_probe.py --task gta6_people
& <venv>\python.exe evals\browser_hybrid_probe.py --prompt "…" --max-turns 30
```

Requires LM Studio serving `google/gemma-4-26b-a4b-qat` (or pass `--model`). Gemma may put answers in `reasoning_content` — the probe reads that.

## Harness parity with production `browser_agent` (#317, 2026-07-04)

Promoted the probe's proven golden tasks (HN, AutoTrader, GTA6, ExampleBiz) into a live,
judged eval against production (`evals/datasets/browser_golden.jsonl`,
`python -m evals.runner --suite browser_golden --live --judge`) — the first head-to-head
of the probe's isolated success against the real `browser_agent` subagent (supervisor
delegation, real Playwright MCP, real tool selection). Initial result: **2/5 passed**
judged. Diagnosing the gap (enabled by adding tool-call **argument** capture to the eval
harness, not just tool names — `evals/live.py` `tool_calls` field) surfaced concrete,
fixable differences from the probe, all filed and fixed under #317:

| Gap vs. the probe | Fix |
|---|---|
| No temperature pin — probe runs `temperature=0.0`; production defaulted to `0.7` and gave different pass/fail on identical re-runs | `model_override.temperature` now wired through (`factory.py`); `browser_agent` pinned to `0.1` |
| Loop-breaker allowed 2 identical failures before blocking; probe bans on the first | `executor_strategy.py` now blocks after the first failure (was `fail_count >= 2`, now `>= 1`) |
| Playwright's raw "ref not found" error gives the model nothing to correct on | Added a stale-ref hint telling the model to re-snapshot instead of guessing |
| No structural honesty gate — probe forces an explicit `terminate(status, answer)` with an enforced "only success with values you actually saw" rule; production had free-form prose and no such gate | Added an explicit "Honesty when verification fails" section to the `browser_agent` Supabase persona (v10): must say "I could not verify" rather than answer with unconfirmed values |
| `browser_agent` was pinned to `gpt-oss-20b` (from an earlier, unrelated fix, issue #287) instead of the model the probe proved out | Repinned to `google/gemma-4-26b-a4b-qat` — same model already driving the supervisor, so no extra JIT load/unload |
| `browser_agent`'s routing description only covered logged-in dashboards, so public/dynamic-content tasks silently went to `web_search` instead of escalating | Widened `AGENT_DESCRIPTIONS`/`WEB_SEARCH_ROUTING_SUPERVISOR` (`web_search_routing.py`) with escalation criteria: interaction required, single-site-only tasks, stale `web_search` results |
| Eval's own grounding judge was unreliable in both directions — truncated tool-result capture (`TOOL_RESULT_CHARS`) failed a genuinely grounded answer once, and separately misread its own truncation marker as "the assistant must have seen the rest" and passed an ungrounded one | Documented larger `EVAL_TOOL_RESULT_CHARS`/`EVAL_TOOL_RESULTS_TOTAL_CHARS` overrides in the dataset header; judge prompt tightening still open |

**Result after fixes: 4/5 passed** (no-judge live rerun) — both cases that previously
hallucinated after a failed navigation (`smallbiz_owners`, `gta6_characters`) now either
succeed cleanly or visibly try harder (fallback tool, longer reasoning) instead of
confidently guessing. Remaining failure (`autotrader_mazda`) is supervisor-level
tool-selection variance, not a `browser_agent` behavior bug — out of scope for #317.

## Future work

1. ~~Full eval suite (#298)~~ — done via #317 (`evals/datasets/browser_golden.jsonl`, live + judge lane).
2. ~~Wire into `browser_agent`~~ — done; `browser_agent` uses the real Playwright MCP + DOM-first SoM pipeline in production, harness-aligned with the probe as of #317.
3. ~~Safety / HITL (#279)~~ — done. Write-action approval cards, read-only default, per-site allowlist, kill-switch. See `docs/features/BROWSER_AUTOMATION.md` → "Computer-use safeguards" and `src/core/orchestration/browser_safety.py`.
4. **Supervisor-level routing determinism** — the supervisor's own tool-selection temperature/variance (separate from `browser_agent`'s, which #317 fixed) still causes occasional mis-routing (e.g. `autotrader_mazda` → `web_search` only in one run). Not filed yet.
5. **Custom widgets** — click-to-open non-native dropdowns; keep vision as fallback when CONTROLS miss controls.
6. **Vision turn discipline** — when an image is attached, force JSON-only replies (models often drift to prose).

## Related

- Fara spike (not promoted): `docs/features/FARA_SPIKE.md`, issue #288 (closed)
- Epic: #274
- Harness parity: #317 (closed)
- Safeguards: #279 (closed)
- Eval lane: #280 / #298 (closed via #317)
