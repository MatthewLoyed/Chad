# Golden-frame replay harness (frontend ↔ backend seam tests)

Storybook tests components in isolation. This harness tests the **seam** between
the Python Brain and the web Limb — the place where "the agent said it's tested"
bugs actually live (prompt tokens not showing, thinking stream flickering, plan
cards duplicating, thinking tokens cut off).

It replays a recorded sequence of wire frames through the **real** production path.
No Brain, no LLM, no hardware, fully deterministic. Runs under Vitest.

## The wall: reducer-driven real-DOM replay

The render now runs entirely on the typed stream protocol. `domReplay.tsx` mounts
the **real** `<ChatStream/>` in jsdom and drives it through the production typed
seam — bridging recorded frames to `StreamEvent`s and pushing them through
`window.applyStreamEvent`, exactly as the Python emitter does:

```
recorded frames → legacyFrameBridge → StreamEvent[]
   → window.applyStreamEvent → streamReducer → streamTurn (store)
   → partsToThinkingEvents → AIResponseRenderer / ThinkingWidget (real render)
```

Measurements are read from the rendered DOM after every event, so flicker (length
regressions) and truncation (lost thinking tokens) are observable.

## Files

- `wireFrames.ts` — recorded-frame types + JSONL parser (mirrors what `main_window.py`
  pushes today; the bridge converts these to `StreamEvent`s).
- `legacyFrameBridge.ts` — `recordedFramesToStreamEvents`: converts recorded frames
  into the typed protocol so existing fixtures drive the reducer/render.
- `frameRecorder.ts` — tee live frames to a downloadable JSONL while a real session
  runs. `installFrameRecorder()` then `downloadRecordedFrames()`.
- `domReplay.tsx` — real-DOM replay: `replayTurnInDomTyped(frames)` mounts `<ChatStream/>`,
  drives the typed seam, reads the rendered DOM; plus `isMonotonicNonDecreasing`.
- `fixtures/*.jsonl` — recorded turns, one frame per line.
- `turnDomReplayTyped.dom.test.tsx` — the wall's assertions (anti-flicker, no-cutoff,
  single plan/tool card, untruncated answer) on every fixture.

Related reducer-level tests live next to the protocol:
`protocol/streamReducer*.test.tsx`, `protocol/partsAdapter.test.ts`,
`runtime/streamSink.dom.test.ts`.

## Capture a new scenario

Dev builds arm the recorder automatically (`main.tsx`, behind `import.meta.env.DEV`), so
it is already capturing before the interesting thing happens — you don't have to predict
the bug and start recording first.

1. Run the desktop GUI (or the web UI against a real Brain).
2. Drive the scenario (send a prompt, let the turn fully finish — wait for `ready`).
3. In devtools: `window.chadFrameCount()` to confirm frames accumulated, then
   `window.chadDownloadFrames('turn_x.jsonl')`.
4. Drop it in `fixtures/` and add a `describe` block in `turnDomReplayTyped.dom.test.tsx`.

`window.chadRecordFrames()` starts capture manually (production builds, or after a reset).

Recordings tee the **typed** seam (`window.applyStreamEvent`) as `ch: 'stream'` frames —
the exact events that drove the render — and `replayTurnInDomTyped` replays those
verbatim. The older hand-authored fixtures carry only the legacy `thinking` channel and
are reconstructed through `legacyFrameBridge`, which is a lossy mirror of what the user
actually saw. A real capture is strictly better; prefer one whenever fidelity matters.

## Why these assertions

| Assertion | Bug class it guards |
|---|---|
| ANTI-FLICKER (monotonic rendered length) | thinking text regresses/flickers mid-turn |
| NO CUTOFF (deltas survive into DOM) | thinking tokens dropped at a boundary |
| SINGLE PLAN | plan + progress update render as two cards |
| FINAL ANSWER / FINALIZE | answer or thoughts lost when the turn is committed |

## How the render works now

Live turn content is one immutable `streamTurn` (`protocol/streamReducer.ts`):
`run_started → *_delta → *_end` events fold into an ordered list of id-keyed parts
(reasoning / text / tool / plan) that can only grow. `partsToThinkingEvents` adapts
that into the flat list `AIResponseRenderer` renders. There is no imperative DOM
append path anymore, so flicker/cutoff are structurally prevented rather than
patched. See `docs/features/STREAMING_REFACTOR.md`.
