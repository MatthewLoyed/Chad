# Quality evals — human-in-the-loop + Gemma-4 judge

**Status:** vision + first seed suite (`evals/datasets/wedding_quality_hitl.jsonl`, gitignored).  
**Goal:** catch **behavioral** failures (bad plans, reasoning spirals from contradictory prompts, ungrounded answers, premature “done”) — not “did the right tool name appear.”

Most day-to-day Chad problems are no longer “forgot to bind Drive.” Multi-tool routing mostly works. What still hurts is **quality of the turn**: planning collapse, prompt contradictions that burn reasoning tokens, confident wrong facts, and long-horizon workflows that stall or fake success.

## What this is (and isn’t)

| Keep (cheap / smoke) | This lane (quality) |
|---|---|
| `tools_any` / `tools_exclude` / budgets | Full transcript judged for behavior |
| Dry-run tool selection | Live / workflow on real Drive / search / code |
| Small-model smoke judges | **Gemma 4** as the quality judge |
| Pass/fail on tool names | Pass/fail on “would a careful human accept this?” |

Coding-quality scoring stays a **separate track** (tests / sandbox outputs). Browser control is a **known limitation** suite (graceful refuse), not a fake-success target.

## Hard safety (eval)

Until a programmatic **eval tool ban list** lands (`platform=eval` refuses execute), cases must:

1. Put hard nos in the **prompt** (no Drive edit/create, no send email).
2. Assert `tools_exclude` on side-effect tools so a violation fails the case.
3. Prefer `--workflow` with auto-“proceed” only for **read / plan / summarize** goals.

Planned follow-up: suite/runner `blocked_tools` that the Brain cannot execute under `platform=eval` (send email, Drive edit/update/delete, etc.).

## Loop

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Run 1–N     │────▶│ Review queue     │────▶│ Gemma-4 judge   │
│ live/       │     │ prompt + full    │     │ full context +  │
│ workflow    │     │ transcript       │     │ optional notes  │
└─────────────┘     └────────┬─────────┘     └────────┬────────┘
                             │                        │
                             ▼                        ▼
                      Human likes /              Blind  |  Directed
                      dislikes / notes           (no notes) | (+ preferred /
                                                     truth / human notes)
```

### Modes

1. **Queue-only** — overnight / long runs store prompt+output packages; no gate.
2. **Blind judge** — Gemma 4 sees full package + behavioral rubric; no preferred answer.
3. **Directed** — same + short truth / “preferred outcome” notes (e.g. wedding fact that must not be invented).
4. **Calibrated HITL** — after human comments, judge re-reads transcript **with** those notes and explains agreement / misses.

Human notes are the calibration signal. String-match `response_includes` stays optional smoke, not the quality definition.

**How to run / judge vs fix:** see [`evals/AGENTS.md`](../../evals/AGENTS.md). Lessons log:
[`evals/HARNESS_LESSONS.md`](../../evals/HARNESS_LESSONS.md). Architecture for observe→fix→retest:
[`CHAD_SELF_EVAL_LOOP.md`](../architecture/CHAD_SELF_EVAL_LOOP.md) (#358).

### Package the judge (and human) should see

- User prompt + case `note` / optional truth hints  
- Assembled system / conflicting instructions (spiral source) when available  
- Plan steps  
- Tool calls + results (truncated carefully, not emptied)  
- Final response  
- Reasoning length / repetition signals when captured  
- Human comment block when present  

Today’s thin rubrics (`response_grounded`, `planner_quality`, `workflow_outcome`) are a start. Next: a dedicated **`behavioral_quality`** rubric aimed at spirals, contradiction-driven thrash, planning collapse, and honesty under limits.

## Seed suite (personal anchors)

`evals/datasets/wedding_quality_hitl.jsonl` (gitignored `wedding_*.jsonl`):

| id | Intent |
|---|---|
| `qh_wedding_roadmap_plan` | Find/read wedding roadmap → briefing; planning + grounding |
| `qh_golfport_docs_brief` | Explore Golfport Drive docs → brief + open questions; no invented files |

Run on **worktree Brain `:8788`** — full bootstrap in [`evals/AGENTS.md`](../../evals/AGENTS.md).
Never use this suite to validate uncommitted Brain code against prod `:8767`.

```powershell
$env:PYTHONPATH = "."
$env:CHAD_SERVER_URL = "ws://localhost:8788/ws"
$env:CHAD_MAIN_ROOT = "C:\Users\ExampleUser\Documents\Coding\Fun\AIAssistant"
# use main venv python
python -m evals.runner --suite wedding_quality_hitl --workflow `
  --judge --judge-model "google/gemma-4-26b-a4b-qat"
```

**Human review:** open `evals/runs/latest.md`, then each  
`evals/runs/transcripts/run_<ts>/<case_id>.md`.  
Leave comments in chat or `evals/runs/review_notes/<case_id>.md` (gitignored later if personal).

## What we want to find

- Reasoning spirals / thrash from **contradictory prompt directions**  
- Planner that looks fine then **stalls, skips, or declares done early**  
- Answers that **don’t follow tool evidence** (esp. Drive content)  
- Soft policy violations (edit/send) even when asked not to  
- Weak briefs: vague, padded, or inventing Golfport/wedding facts  

## Non-goals (for this lane)

- Growing multi-tool “did it call X” coverage  
- Deep-research spam cases  
- Treating coding quality as the same rubric  
- Pretending browser control works  

## Implementation backlog (short)

1. Review-queue folder + “look here” index after each quality run  
2. `behavioral_quality` judge rubric + Gemma-4 default for this suite only  
3. Optional `truth` / `human_notes` fields on cases  
4. Programmatic `blocked_tools` under `platform=eval`  
5. Overnight queue that accumulates pairs without requiring a live human  
6. Keep coding / browser as sibling suites with different scorers  

## First seed run (2026-07-24)

Target: worktree Brain `:8788` (`k99b`, `CHAD_SECRETS_PATH` + main `.env`). Suite:
`wedding_quality_hitl` × `--workflow`. **Default quality judge: `google/gemma-4-26b-a4b-qat`**
(not 31b — 31b OOMs when the agent model is resident). Re-judge without re-running Brain:

```powershell
python -m evals.tools.rejudge_run --run 20260724_194302 --suite wedding_quality_hitl `
  --judge-model "google/gemma-4-26b-a4b-qat"
```

| Case | Wall | Tools | Plan steps | Auto gate | Judge (first pass) |
|---|---|---|---|---|---|
| `qh_wedding_roadmap_plan` | ~96s | 1× `google_drive_read_file` | **0** | PASS | inconclusive (31b VRAM) |
| `qh_golfport_docs_brief` | ~224s | traversal + search/list/read (~22 top-level) | **0** | PASS | same |

**Takeaways for the HITL loop**

- Automated **100% PASS is not quality signal** when judges never scored; `tools_exclude` /
  `must_complete` only prove “didn’t blow up / didn’t send mail.”
- **`category: planning` produced 0 plan steps** on both — workflow completed on the agent path
  without a captured planner artifact. That is exactly the kind of behavioral gap this lane
  exists to surface (and why human review of transcripts matters before we trust a green report).
- **Use Gemma-4-26b for quality judging.** Keep 31b out of the co-resident judge path.
- Human review paths for this run:
  - `evals/runs/transcripts/run_20260724_194302/qh_wedding_roadmap_plan.md`
  - `evals/runs/transcripts/run_20260724_194302/qh_golfport_docs_brief.md`

### Human notes — `qh_wedding_roadmap_plan` (2026-07-24)

- Did **not** actually run in planning mode (0 plan steps) — ran as **agent**. Root cause:
  `TurnModeResolver` forced `agent` for every `platform="eval"` turn, ignoring harness
  `category=planning`. Fixed: eval/system turns now honor an explicit category.
- For **agent** mode: output was **great** and **very efficient** (one Drive read → briefing).
- Task was barely one step — easy for Chad; still useful as a quality / grounding calibration.
- Eval output gaps called out (fixed in same pass):
  - Thinking was isolated instead of chronological (`thinking → tool → thinking`).
  - Truncation hid raw tool bodies from the judge — quality lane now keeps full results +
    writes a raw `.json` sidecar next to each transcript.

### Dinner batch (2026-07-24 evening) — see `evals/runs/review_queue/DINNER_LOOK_HERE.md`

- Plan lane **works** (`plan_steps=3` on both live plan cases) but final response omits the plan
  text (~87 chars) → Gemma-26b fails `response_grounded` (fair).
- `--workflow` was remapping `agent`→`planning` (fixed) which re-introduced re-plan thrash.
- Agent suite 4/4 auto-pass (wedding/Golfport sites, Gmail+Drive brief, memory) — human skim
  still required; several judge calls returned empty/`score=null` while marked pass.  
