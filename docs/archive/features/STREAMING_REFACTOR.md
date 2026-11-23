# Streaming Refactor — Brain→Limb→React seam

Living doc for the multi-phase effort to make the desktop GUI's data streaming
robust: no flicker, no truncated thinking tokens, consistent processing/reasoning/
outputting state. Branch: `claude/trusting-poitras-19dbf8`.

## Why this exists (diagnosis)

A single token crosses **4 hops and 2 languages** before render:

1. **Brain** (`src/remote/server.py` / orchestrator) → `ChadMessage` over WebSocket
   (`AI_THOUGHT`, `AI_RESPONSE` with a `fragment` flag, `EXECUTION_PLAN`, `TOKEN_USAGE`).
2. **Limb (Python)** — `src/remote/gui_limb.py` `handle_message()` decodes a stringly-typed
   `state` field via a big if/elif ladder, buffers into `_speech_buffer` / `_turn_thinking_events`.
3. **MainWindow** — `src/clients/desktop/main_window.py` `push_*()` fires
   `webview_window.evaluate_js("window.handle*(...)")` — **fire-and-forget, unordered, no ack**.
4. **React** — `window.handle*` handlers split into **two rival render paths**.

**Root cause: no single source of truth.** The same token data is reassembled 5+ times
with different merge rules:
- **Imperative DOM path** (perf): thinking/speech deltas → `window._thinkingQueue` →
  rAF drain → `appendChild(createTextNode())` straight into DOM nodes via refs, bypassing
  React. `StreamingRawText` is `memo(() => true)` so React never wipes the hand-appended nodes.
- **React/Zustand path**: `activeTurn.events`, `streamingResponse`, finalized `chatMessages[].thoughts`.

The **handover between these two** produces every symptom:
- **Cutoff** — thinking lives only in the DOM during a turn; committed to the store once at
  turn-end via `flushThinkingStream()`. If the flush reads an empty DOM (queue not yet drained)
  or `status==='ready'` clears the DOM first, tokens vanish.
- **Flicker** — `streamingResponse` toggles `display:none` on the raw stream while the Markdown
  version mounts; refs clear/reset; the active-turn block mounts/unmounts; `AnimatePresence`
  animates every change.
- **Inconsistent state** — `status` is written by 4 racing sources: Python `update_status`,
  `handleStateUpdate`, a **3s `get_raw_state` poll that overwrites just-set state**
  (`App.tsx` checkApi), and React-local `thinkingStarted`/`hasStartedStreaming` booleans.

## Target model (from research: AG-UI + Vercel AI SDK)

Both converge on the inverse of Chad's design:
- Every stream is **`start → delta → end`**, each carrying a **stable id** (messageId/
  toolCallId/reasoning id).
- Deltas accumulate into **one immutable message** with an **ordered list of typed parts**,
  applied by **one reducer**. **No manual DOM.**
- Reasoning and text are **separate id'd streams**.
- Flicker/cutoff prevention falls out *for free*: an id'd part can only grow; `end` finalizes;
  nothing else can wipe it.

Chad already half-imports an "AG-UI-inspired" normalizer (`src/clients/web/src/protocol/turnProtocol.ts`).

## Phases

- **Phase 0 — Regression wall (DONE).** Make the harness exercise the REAL seam, lock behavior.
- **Phase 1 — Unified typed wire protocol** (Python emitter + TS reducer), transport unchanged.
- **Phase 2 — Single reducer-driven render**: retire the imperative DOM bypass, add an explicit
  turn state machine, delete the 3s poll overwrite and the `display:none` handover.
- **Phase 3 — Ordered transport**: seq-gap detection; typed `postMessage` replacing fire-and-forget
  `evaluate_js`.
- **Phase 4 — Delete dead reassembly paths**, strip remaining debug logs, consolidate.

Each phase must keep the Phase 0 wall green; gate in CI.

## Phase 0 — what shipped (commits on the branch)

- `5d82743` — Extracted App.tsx stream glue → **`src/clients/web/src/runtime/streamRuntime.ts`**
  (`installStreamHandlers` / `startDrainLoop` / `drainStreamQueues` / `flushThinkingStream` /
  `extractTextFromContent`). App.tsx imports it — the live app and the harness now run the SAME
  code, closing the long-standing "harness re-implements the glue" gap. Added real-DOM harness
  + the `state` frame fidelity fix (recorder never captured `handleStateUpdate`).
- `c5071b5` — tool-use shape fixture.
- (widen) — interleaved-speech + rapid-burst fixtures; `drainMode:'deferred'` in the harness.
- `bcb7030` — **fix: stop dropping thinking tokens on single-frame turns.** `flushThinkingStream`
  now drains the queue before reading; `handleStateUpdate` no longer clears `_thinkingQueue`
  before flushing. First real user-symptom bug found AND fixed via the wall.

### The test wall (use it every phase)

`src/clients/web/src/testkit/` — see its `README.md`. Two tiers:
- **Store tier** — `replayTurn.ts` → `turnReplay.test.ts` (node env): reducer/merge/plan/token contract.
- **Real-DOM tier** — `domReplay.tsx` → `turnDomReplay.dom.test.tsx` (jsdom): mounts the real
  `<ChatStream/>`, drives the real seam, reads rendered DOM. Asserts anti-flicker (monotonic
  rendered length), no-cutoff, single plan/tool card, untruncated answer. `drainMode:'deferred'`
  simulates a whole turn landing in one animation frame (the race class).

Run: `cd src/clients/web && npx vitest run` (30 tests). Typecheck: `npx tsc -b`. Lint: `npx eslint <files>`.

Capture a REAL fixture from a live session: devtools →
`import('/src/testkit/frameRecorder.ts').then(m => m.installFrameRecorder())`, run a turn,
`...downloadRecordedFrames('turn_x.jsonl')`, drop in `fixtures/`, add a `describe`.

### Caveats / gotchas
- **jsdom + framer-motion**: `AnimatePresence` exit never completes, so the active-thinking block
  lingers after finalize (2 `.chat-message--ai`). Element-count assertions must tolerate this
  (the ghost block is empty). `readFinalAnswer` scans all AI bubbles for this reason.
- **Test deps** installed with `--legacy-peer-deps` (storybook wants Vite 6, project is on Vite 7).
- **CRLF**: git warns LF→CRLF on the new files; harmless on Windows.
- A single tool call renders `.tool-bubble`; `.tool-stack-wrapper` is only for multi-call stacks.

## Phase 1 — concrete plan

Goal: one typed, id-correlated event schema shared Python↔TS; one emitter, one reducer.

1. **Define the schema** (TS keystone) — **DONE.** `src/clients/web/src/protocol/streamEvents.ts`:
   discriminated union (`run_started | reasoning_delta | reasoning_end | text_delta | text_end |
   tool_start | tool_end | plan_snapshot | token_usage | run_finished | run_error`), every event
   carrying `turn_id` + monotonic `seq`, explicit `delta` vs `snapshot` semantics, `detectSeqGap`.
3. **TS reducer** — **DONE.** `src/clients/web/src/protocol/streamReducer.ts`: pure
   `applyStreamEvent(state, event)` folds the union into ONE immutable `TurnState` — an ordered
   list of id-keyed typed parts (`reasoning | text | tool | plan`) that can only grow. Derive
   helpers `reasoningText / answerText / toolParts / planParts`. Designed to replace the 5
   reassembly sites (`turnEventReducer`, `store.addThinkingEvent` merge, `ThinkingWidget.displayEvents`,
   `AIResponseRenderer.buildTree`, gui_limb `_consolidate_and_sanitize_thoughts`) — not yet wired.
4. **Bridge old→new + equivalence proof** — **DONE.** `src/clients/web/src/testkit/legacyFrameBridge.ts`
   converts the current `RecordedFrame`s into `StreamEvent`s; `src/clients/web/src/protocol/streamReducer.equiv.dom.test.tsx`
   drives every Phase 0 fixture through bridge→reducer and asserts the reducer reproduces the REAL
   seam's rendered output (thinking text, answer text, tool/plan structure, plan replace-semantics,
   append-only monotonicity). **62 web tests green.** This is the gate: the typed path is proven
   behavior-equivalent on recorded reality before any production swap.
   - Bridge part-boundary rules worth knowing: consecutive reasoning frames coalesce into one
     reasoning part (any non-reasoning event closes it); caption deltas accumulate into the current
     text part, a `speech`(append:false) finalizes it; `activity_snapshot` PLAN frames map to a
     single per-turn `plan_id` (replace semantics → one card); a turn that streams no captions/speech
     and carries its answer only in `run_finished.response` synthesizes one text part from it.
2. **Python emitter** — **DONE (inert, flag-gated).** `src/remote/stream_events.py`:
   `StreamEventEmitter` — pure, dependency-free, mirrors the TS bridge's part-boundary rules
   (reasoning coalescing, text-part lifecycle, PLAN→one per-turn plan_id, response-only synthesis)
   and stamps `turn_id`+monotonic `seq`. Unit-tested in `tests/unit/test_stream_events.py` (11 tests,
   verifiable: `PYTHONPATH="." .venv\Scripts\python -m pytest tests/unit/test_stream_events.py -q`).
   Wired into `gui_limb.handle_message` **additively and behind `CHAD_STREAM_EVENTS` (default off)**:
   when enabled it runs PARALLEL to every legacy `push_*` call via the exception-safe `_se()` helper,
   pushing batches through the new `main_window.push_stream_event` → a `typeof`-guarded
   `window.applyStreamEvent(events)`. **Prod behavior is unchanged** until both the flag is set AND
   the Phase 2 reducer handler is installed (the guarded JS no-ops today). Feed sites: reasoning,
   tool start/end, caption→text_delta, speech finalize (interleave + final), EXECUTION_PLAN→plan_snapshot,
   token_usage, run_finished, run_error; emitter (re)started in `send_input`, cleared at turn end/error.
   **Verify live:** set `CHAD_STREAM_EVENTS=1` on the Limb, run a turn, confirm legacy streaming is
   unaffected and (once Phase 2 lands `window.applyStreamEvent`) the typed events drive the reducer.
5. Keep the wall green throughout; add reducer unit tests. **DONE** (TS equivalence suite + Phase 0 wall
   + Python emitter unit tests).

**Phase 1 is complete.** The typed protocol exists end-to-end (Python emitter → wire → TS reducer),
is proven behavior-equivalent to the legacy seam on recorded reality, and is inert in prod. Phase 2
flips it on: install `window.applyStreamEvent` (fold the event batch through `applyStreamEvent`),
render from the reducer's `TurnState.parts`, then retire the imperative DOM bypass / 3s poll / display:none handover.

## Phase 2 — progress

**Phase 2a — DONE (sink wired).** `store.streamTurn: TurnState | null` + `applyStreamEvents(events)`
action (folds a batch through `applyStreamEvent`); `streamRuntime.installStreamHandlers` registers
`window.applyStreamEvent` (the sink the Python emitter drives via `main_window.push_stream_event`).
`runtime/streamSink.dom.test.ts` proves the production sink folds identically to the pure reducer for
every fixture, including worst-case one-event-at-a-time delivery. Parallel to render — no behavior change.

**Phase 2b — DONE (render adapter proven).** `protocol/streamReducer.partsToThinkingEvents(state)` maps
the reduced `TurnState` back into the flat `ThinkingEvent[]` the existing `AIResponseRenderer`/`ThinkingWidget`
consume (reasoning→reasoning, text→speech, tool→resolved tool_start, plan→execution_plan). `protocol/partsAdapter.test.ts`
locks the contract on all fixtures. **This makes the production swap a near one-liner.**

**Phase 2c — DONE (reducer-driven render is now the default).** `ChatStream` renders from
`partsToThinkingEvents(streamTurn)` when `streamTurn` is active; the legacy imperative answer blocks are
gated off then so the answer isn't doubled; finalizing an AI message clears `streamTurn`. The Python emitter
is **default ON** (`CHAD_STREAM_EVENTS`, set `=0` to revert to pure legacy render). Wall coverage:
`turnDomReplayTyped.dom.test.tsx` drives the real `<ChatStream/>` through `window.applyStreamEvent` and
asserts the same anti-flicker/no-cutoff/single-card invariants on all 4 shapes; `turnDomReplayDual.dom.test.tsx`
drives BOTH paths at once (as prod does) and asserts no duplication. The `_se()` calls sit beside every legacy
`push_*`, so the typed path receives byte-identical input — render equivalence then follows from the wall.

**Phase 4 — DONE (legacy render path deleted).** The imperative-DOM bypass is gone: `window.appendStreamToken`/
`appendThinkingToken`, the token/thinking rAF queues + drain loop, `flushThinkingStream`, the raw-text refs and
their `window._*` globals, the `display:none` raw↔markdown handover, and `streamRuntime`'s `handleCaptionUpdate`/
streaming `handleThinkingEvent` are all removed. ChatStream renders the active turn solely from `streamTurn` via
`partsToThinkingEvents`; live-turn booleans derive from the render events (no `hasStartedStreaming`/`thinkingStarted`
state). The Python emitter is unconditional (the `CHAD_STREAM_EVENTS` flag is removed). `streamRuntime`'s remaining
`window.handle*` carry only out-of-band signals (status, status text, token usage, audio, finalize, and the legacy
thinking channel reduced to autonomous-notification + model_loading). The legacy DOM/store walls and the dual-path
test were removed (the typed wall `turnDomReplayTyped.dom.test.tsx` is the wall now); `addChatMessage` sources
finalized-bubble thoughts from `streamTurn`. `gui_limb` still emits the legacy `push_thinking_event`/`push_caption`
(typeof-guarded); `activeTurn` remains a vestigial store slice used only by storybook. **66 web + 11 Python tests
green.**

**`push_thinking_event` is NOT dead — it's just narrower than its name suggests (issue #636, 2026-07-25).**
`gui_limb.push_thinking_event()` still gets called for every event type (reasoning, tool_start, plan snapshots,
speech, autonomous_notification, model_loading — anything the orchestrator emits), but as of #636
`MainWindow.push_thinking_event()` in `src/clients/desktop/main_window.py` drops everything except
`autonomous_notification`/`model_loading` *before* serializing, because the main window's
`window.handleThinkingEvent` (`streamRuntime.ts`) only ever reads those two — live reasoning/tools/speech render
from the typed `window.applyStreamEvent` seam instead (see Phase 4 above). This is a perf fix (skip
JSON-serialize + cross-process `evaluate_js` for events nobody reads), not a deprecation: the method, the JS
handler, and the channel are all still live for the 2 types they actually carry.

Correction to the note above: the HUD overlay (`chad-hud/`, Electron, loads `?mode=hud`) does **not** receive
events through this pywebview bridge at all — it runs as a fully separate process connected over its own
websocket transport (`src/clients/web/src/transport/brainSocket.ts`), which independently forwards
`autonomous_notification` to the same `window.handleThinkingEvent` global inside that process. `gui_limb.window`
is always the single `MainWindow` instance (`src/remote/gui_limb.py:46`); there is no fan-out to the HUD from
here. Don't confuse it with the phantom overlay in
`src/core/interfaces/overlay_manager.py` either, which uses its own `updateContent()`/`playEntrance()` JS and
never touches `handleThinkingEvent` at all. Both "HUD-ish" things are separate, and neither is fed by
`MainWindow.push_thinking_event` beyond the main window itself.

**The one thing still worth doing live:** a smoke test on the real Brain+GUI (a few turns: reasoning, a tool, a
plan, a streamed answer) and capturing a REAL fixture (frameRecorder) to re-run the typed wall against — today's
fixtures are hand-authored to the wire shapes, the only verification gap left. Optional follow-ups:

1. **Kill the imperative DOM bypass**: render thinking/text from the reducer's parts; keep rAF
   coalescing as a buffered store slice feeding the SAME reducer (DOM/state can't diverge).
   Delete `appendThinkingToken`/`appendStreamToken` direct-DOM, `StreamingRawText` `memo(()=>true)`,
   the `display:none` raw-vs-markdown swap, and the ref plumbing (`_thinkingStreamRef` etc.).
2. **Explicit turn state machine** (`idle→thinking→tool→responding→done`); derive `status` from it.
3. **Delete the 3s `get_raw_state` poll overwrite** in `App.tsx` checkApi (item 2) — replace with
   event-driven status + a single reconciliation on reconnect.
4. Re-point ThinkingWidget/ChatStream/AIResponseRenderer at the reducer parts.

## Key files
- `src/clients/web/src/runtime/streamRuntime.ts` — the extracted seam glue (Phase 0).
- `src/clients/web/src/protocol/streamEvents.ts` — target typed schema (Phase 1 keystone).
- `src/clients/web/src/protocol/turnProtocol.ts` — current normalizer (to be subsumed).
- `src/clients/web/src/reducers/turnEventReducer.ts` — current reducer (to be replaced).
- `src/clients/web/src/store.ts` — Zustand store + `addThinkingEvent` merge (to be simplified).
- `src/clients/web/src/components/layout/ChatStream.tsx` — render + imperative DOM (to be reworked).
- `src/clients/web/src/components/data-display/ThinkingWidget.tsx` — thinking render.
- `src/clients/web/src/App.tsx` — handler install + the 3s poll.
- `src/remote/gui_limb.py`, `src/clients/desktop/main_window.py` — Python seam (Phase 1 emitter).
- `src/clients/web/src/testkit/` — the wall.

Project context also recorded in agent memory: `streaming-refactor-initiative.md`.
