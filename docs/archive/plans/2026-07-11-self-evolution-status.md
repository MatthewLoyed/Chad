# Self-Evolution (Reflective Lessons) — Status & Next Steps

**Last verified:** 2026-07-11 (against `master` + branch `claude/agentic-skills-research-fb464b`).
**Priority:** not top-priority, but keep it documented and pick it up later. This doc is the
single place to resume from.

Reflective Lessons = Chad reflects over recent runs and writes durable, readable lessons
("Matthew's lamp maps to device 'bedroom'"; "Matthew wants the conclusion first") that get
injected into future prompts — slow, honest self-improvement. Distinct from OATS (which only
nudges tool *selection* via embeddings). See also the memory notes `reflective-lessons-design`
and `oats-self-improving-tool-selection`.

## What is DONE (verified — built, wired, tested)

The full propose → gate → gated-write pipeline exists and is live. **39 unit tests pass**
(`tests/unit/test_reflection_{block,dedup,store_local,trigger}.py`).

| Piece | Where | State |
|---|---|---|
| Reflect (judge proposes scoped lessons + questions, reads tool RESULTS) | `src/core/intelligence/reflection.py` `reflect()` | ✅ |
| Gatekeeper (schema, confidence floor, dead-tool, suppression, dedup, cap) | `src/core/intelligence/lesson_gatekeeper.py` | ✅ |
| Graph cross-dedup (drop facts already in the knowledge graph) | `reflection.py` `_drop_graph_duplicates()` | ✅ |
| Propose (read-only diff) + gated apply (`apply_admitted`) | `reflection.py` | ✅ |
| CLI: dry-run by default, `--write` needs typed `WRITE` confirm | `reflection.py` `main()` | ✅ |
| Heartbeat propose-only cycle (interval, GUI review queue, per-lesson `auto_write_confidence_floor`) | `src/core/monitoring/heartbeat_service.py` (~L210–260) | ✅ wired |
| Thumbs-down → dry-run trigger | `src/core/intelligence/reflection_trigger.py` + `src/remote/remote_factory.py:209` | ✅ wired |
| Review/approve queue (per-item apply, answer→graph, dedup, event, local-first shim) | `src/core/intelligence/reflection_lessons_store.py` + migration `012_create_reflection_lessons.sql` | ✅ |
| Scope routing | `user` → knowledge graph; `tools` → persona `tools` record | ✅ (old open design-Q resolved) |

**Config keys** (all off by default): `reflection.enabled`, `reflection.interval_hours`,
`reflection.model`, `reflection.convos`, `reflection.confidence_floor`,
`reflection.auto_write_confidence_floor` (unset = never auto-write; every lesson waits for
GUI/CLI approval).

**Safety posture (already enforced):** nothing writes on import or by default; batch write
needs typed confirm; heartbeat auto-write is opt-in per-lesson via the floor and stays visible
in the GUI; both stores are reversible (persona_config is versioned; graph facts can be
`forget()`-ten); lessons must carry a FACT, never suppress a behavior.

## What REMAINS — Step 5: make the self-eval honest

This is the one missing piece, and it is the epic's differentiator ("keep the self-eval honest,
unlike Hermes"). Today a written lesson is *assumed* to help; nothing proves it, and rollback is
manual. Two deliverables:

### 5a. Lesson-replay eval (prove a lesson helps)
Same discipline as the OATS replay that proved rank 1.4→1.02.
1. When a lesson is admitted, capture the conversation(s)/turn(s) that motivated it as a replay
   fixture (the reflection log already has convo ids — persist them on the `reflection_lessons`
   row or alongside the proposal).
2. Re-run those turns **with** vs **without** the lesson injected and diff the outcome (tool
   choice / grounding / a judge verdict). Model this on `evals/replay_lane.py` (the OATS
   tool-selection replay) — it's the closest existing harness.
3. Surface a per-lesson "helped / no-change / hurt" verdict in the proposal report
   (`evals/runs/reflect_proposal_latest.md`) and on the GUI queue row.

### 5b. Poisoning canary (catch a lesson that degrades behavior)
1. Curate a small fixed set of known-good behaviors (a handful of prompts with expected
   tool/response assertions) — a canary suite.
2. After any lesson write (auto or approved), re-run the canary. If a lesson regresses it, flag
   the lesson and auto-revert (persona version rollback / graph `forget()`) instead of letting
   it compound.
3. Gate `auto_write_confidence_floor` on a clean canary — auto-write only when the canary stays
   green.

### Then (later, lower priority)
- **Lesson GC / refiner pass** — periodically re-validate OLD stored lessons as tools/prefs
  change (mirror the `--prompt-audit` lane). Already sketched in the original design.
- Consider promoting `provisional` lessons to `active` only after N clean canary runs.

## How to resume (quick start)
```powershell
# Dry-run a reflection cycle over recent convos (writes NOTHING):
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m src.core.intelligence.reflection --convos 15
#   → review evals/runs/reflect_proposal_latest.md
# The tests to keep green while working on step 5:
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_reflection_*.py -q
```
Start step 5 by reading `evals/replay_lane.py` and `reflection.propose_lessons()` /
`LessonProposal` — the replay fixture needs the convo ids that produced each admitted lesson.

## NOT in scope (deliberately deferred)
Prompt-rewriting (too risky), a bandit layer, and parameter learning — all previously ruled out.
Keep lessons symbolic, readable, and reversible.
