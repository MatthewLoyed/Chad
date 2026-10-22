# Capability Self-Improvement

Chad notices when he's missing a capability (or repeating one) and proactively suggests
building a new tool or skill for it. Two signals, sharing the same gatekeeper, storage,
and event side-channel:

- **"blocked"** — Chad *lacks* a tool (recurring `search_for_tool` misses) → suggest a tool/skill.
- **"unlocked"** — Chad *has* the tools but keeps hand-stitching the same multi-tool
  procedure across turns → suggest collapsing it into one skill.

Both are built. Suggestions land in their own side-channel (`capability_suggestions` +
`CapabilitySuggestionRaised`), never injected into active chat.

## Why this signal (and not "no tools selected")

The obvious idea — watch the tool selector for "zero tools chosen" — is a poor signal.
The registry has four fallback layers (LLM selector → RAG → core-tool injection → full
catalog), so Chad almost always gets *some* tool and improvises. True zero-schema events
are basically a system fault, not a capability admission.

Instead we watch [`search_for_tool`](../../src/tools/system/utilities/tool_search.py).
When Chad *deliberately* calls it and the registry returns nothing, that is his own
explicit admission that the capability he wants doesn't exist — a far higher-precision
signal than anything inferred from the outside.

## Pipeline

This mirrors the Propose → Synthesize → Verify shape that CRAFT, SkillWeaver, and
SkillCraft converged on, and the lesson-gatekeeper philosophy already in the repo
(deterministic guards first, LLM judgment only where it earns its keep).

1. **Log** — every `search_for_tool` miss → `capability_gap_log` (Supabase). Survives
   short sessions, so evidence accumulates across runs. Cheap fire-and-forget insert on
   the hot path; the query is embedded with the already-loaded RAG model.
2. **Cluster** (`cluster_gaps`, pure) — groups recurring misses by embedding similarity
   so paraphrases count as one gap. Deterministic, no LLM, unit-tested.
3. **Draft** — only once a cluster recurs `>= min_cluster_size` (default 3) does a small
   LLM judge draft a concrete `{kind, title, description, rationale, confidence}`.
   Repetition is what makes the suggestion accurate rather than a one-shot guess.
4. **Gatekeep** (`SuggestionGatekeeper`, pure) — rejects low-confidence drafts, anything
   that duplicates a live tool/skill (never suggest rebuilding what exists), and
   near-duplicate pending suggestions.
5. **Surface** — admits are stored `pending` in `capability_suggestions` and a
   `CapabilitySuggestionRaised` event is published. **Nothing auto-writes code.**

### Extension point: execute-and-verify (stage 6, not yet wired)

LLM self-judgment has a documented false-positive bias (Voyager). The analyzer exposes a
`verify_fn` hook to run a drafted tool in the sandbox and check it produces a real result
before surfacing — the objective check that pairs with the LLM draft. Intentionally left
unwired in Phase 1.

## Components

| Concern | Location |
|---|---|
| Hot-path logging | `ToolSearchTool._log_capability_gap` ([tool_search.py](../../src/tools/system/utilities/tool_search.py)) |
| Core logic | [`src/core/intelligence/capability_gaps.py`](../../src/core/intelligence/capability_gaps.py) |
| Embedding helper | `SemanticToolSelector.embed_text` |
| Tables | `migrations/008_create_capability_gap_tables.sql` |
| Event | `CapabilitySuggestionRaised` (`src/application/events/events.py`) |
| Tests | `tests/unit/test_capability_gaps.py` |

## Trigger

`CapabilityGapTracker` (mirrors `OATSRefiner`) counts logged gaps and launches a
background analysis pass once `target_count` (default 3) new misses accumulate. No
heartbeat required — it rides the normal turn path, so it works even in short sessions.

`CHAD_CAPABILITY_JUDGE_MODEL` overrides the draft model (default `qwen/qwen3.5-9b`).

## Surfacing / GUI

`get_pending_suggestions()` and `set_suggestion_status(id, status)` expose the
pending → shown → accepted | dismissed lifecycle. `SuggestionsModal`
(`src/clients/web/src/components/modals/settings/SuggestionsModal.tsx`) is the
side-panel (opened from the `ControlDock` lightbulb icon, badge-counted by pending
suggestions) that renders the queue and lets the user accept/dismiss/build a
suggestion without touching code.

`CapabilitySuggestionRaised` pushes live: `UIStateSyncer` (`src/remote/ui_state_syncer.py`)
subscribes it and broadcasts `MessageType.CAPABILITY_SUGGESTION` over the active
websocket; `GUIChadLimb` (desktop) forwards it to `MainWindow.push_capability_suggestion`,
and `brainSocket.ts` (browser/PWA) forwards it to `window.handleCapabilitySuggestion` —
both land in `upsertCapabilitySuggestion` so the badge/panel update without a re-fetch.
The panel also polls `get_capability_suggestions()` on open as a correctness backstop.

## The "unlocked" signal

[`src/core/intelligence/capability_patterns.py`](../../src/core/intelligence/capability_patterns.py)
reuses the gap signal's gatekeeper, persistence, and event. It mines `tool_outcome_log`
(no new logging): successful rows grouped by `turn_id` give each turn's tool sequence
(`extract_tool_sequences`), turns are grouped by their distinct tool-SET and counted
(`count_patterns` — set-based for recall, the judge sees real orderings), and once a
procedure recurs across `>= min_occurrences` (default 3) turns a judge drafts a *skill*
suggestion. The recurring fingerprint is stored in the suggestion's `evidence` so a
stable pattern is judged at most once. `WorkflowPatternTracker.note_turn` is fired from
the orchestrator's turn finalize (fire-and-forget) on any turn with ≥2 distinct
successful tools, and mines every 8 such turns. Plumbing tools (`search_for_tool`,
`ask_user_question`, …) are excluded so they don't manufacture spurious patterns.

## Not yet done

- Stage-6 sandbox verification (`verify_fn`) — execute a drafted capability and check it
  produces a real result before surfacing (the objective check for LLM false-positives).
  Deferred (see [#360](https://github.com/MatthewLoyed/AIAssistant/issues/360)) until
  real suggestion volume/quality can be observed through the now-live panel — and even
  then only applies to the subset of capabilities that don't need an API key/OAuth/live
  hardware to execute.
