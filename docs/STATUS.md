# Project status and priorities

Status as of 2026-11-23. This page is the one place that ranks work; it
replaces the dated strategy, release-plan and triage pages now in
[`archive/strategy/`](archive/strategy/). Details of each item live in its
GitHub issue or owning guide, not here. Update this page when priorities change;
do not add a second roadmap.

## Posture

Chad is a personal, local-first project. General cloud assistants are broader
and more capable, so Chad does not try to match them feature for feature. Work
is justified when it (a) teaches something about agent harnesses on small local
models, or (b) does something that needs to stay local: private memory, voice on
the device, and control of this Windows machine.

The public Windows alpha (v0.2.0-alpha.14) stays available. It is in maintenance
mode: keep it installable and honest, but do not grow the packaging or onboarding
surface.

Focus pillars, in order: **memory and personal recall**, **voice**, and
**integrations and agentic tasks**. The coding harness (Chad Code / OpenHands),
browser controller and Chat/Work mode exploration are parked.

## Current state

| Area | State | Evidence |
|---|---|---|
| Offline checks | Green: 2892 unit tests pass, 2 skipped, with no model server | `pytest tests/unit/`, 2026-11-23 |
| Docs | 67 active pages, 0 broken links (was 118) | `scripts/dev/check_docs.py` |
| Released build | alpha.14 installer; no in-app installer update | [#43](https://github.com/MatthewLoyed/Chad/issues/43) |
| Integration branch | `dev`, recent work landed as small PRs #57–#66 | `git log origin/dev` |
| Unlanded work | Three branches hold real changes; see below | — |

## Priorities

Items marked **offline** need no LM Studio or other model server. Unit tests
mock model transports, so offline work can still be verified. Items marked
**live** need a running local model and should wait until one is available.

### Now (offline)

1. **Land the cause-brief fixes.** `sync_chad_repo_branches` holds one commit
   (34 files): prompt date grounding, fail-closed approval, cron task dedup,
   local model dedup and GUI cleanup, with tests. Its parent is on `dev` and it
   merges cleanly. Review it, run the unit suite, and open a PR.
2. **Port the live voice MVP.** `feature/live-voice-mode` (one commit, 89 files,
   about 5.7k lines) adds native live voice with fast delegation and voice-first
   model residency. It was cut from an older history and has no merge base with
   `dev`, so cherry-pick or port it rather than merging. Its residency and
   format tests run offline; live listening waits for a model.
3. **Resolve #43 honestly.** Either make in-app Update Chad run the next
   installer, or hide it in packaged builds. Hiding it is the cheaper choice
   under maintenance mode.
4. **Finish prompt centralization ([#54](https://github.com/MatthewLoyed/Chad/issues/54)).**
   Move the remaining inline system prompts and model-facing hints into
   `prompts/`. Pure refactor, covered by `evals.runner --prompt-audit`.
5. **Memory groundwork ([#14](https://github.com/MatthewLoyed/Chad/issues/14)).**
   Build offline fixtures for save-initiation and noise rejection, and the
   context-budget accounting, so the live re-measurement is a single run.
6. **Integration contracts.** Continue the pattern from #58, #62 and #64:
   validate tool arguments before dispatch and give empty results clear
   feedback. Each fix is small and unit-testable.

### Next (live model required)

- Re-measure memory write initiation, then improve it through the existing
  approval path (#14). Consolidation follows only after that measurement.
- Live voice latency and quality evals for the ported MVP. Evaluate
  LFM3-Audio-2B as a single on-device voice model
  ([#35](https://github.com/MatthewLoyed/Chad/issues/35)).
- Planner and Scout live runs on real personal tasks (calendar, Canvas, mail).
- Narrated motion explainer ([#52](https://github.com/MatthewLoyed/Chad/issues/52))
  and iPhone control through Poki ([#56](https://github.com/MatthewLoyed/Chad/issues/56)).

### Parked

- Coding harness: a local staged-eval branch keeps five OpenHands
  verification commits, cut from the older history. Port them only if coding
  becomes a focus again.
- Browser controller: stays off (`orchestration.browser_controller_enabled`).
  See the [comparison results](archive/strategy/browser-agent-progress-results.md).
- Chat/Work mode ([#13](https://github.com/MatthewLoyed/Chad/issues/13)), voice
  orb character ([#36](https://github.com/MatthewLoyed/Chad/issues/36)), and
  hardware model recommendations beyond what shipped
  ([#5](https://github.com/MatthewLoyed/Chad/issues/5)).

## Housekeeping backlog (offline, low priority)

- Replace deprecated Pydantic `.copy()` with `.model_copy()` in
  `src/core/orchestration/nodes/planner_node.py`.
- Merge `docs/local-model-rules.md` into `features/LOCAL_MODELS.md`, and
  `architecture/WEB_SEARCH_FETCH_TIERS.md` plus `features/WEB_SEARCH_MEDIA.md`
  into `features/WEB_SEARCH.md`.
- Remove the Render/ngrok-era deployment text from
  `features/TELEGRAM_INTEGRATION.md`, and trim the dated status tiers from
  `architecture/CHAD_CODE.md`.
- Prune merged local branches and stale worktrees regularly.
  `git merge-tree --write-tree origin/dev <branch>` matching
  `origin/dev^{tree}` shows that a squash-merged branch is safe to delete.
