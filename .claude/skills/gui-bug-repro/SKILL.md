---
name: gui-bug-repro
description: Use when the user describes something visually wrong in Chad's GUI — a card showing twice, text flickering or getting cut off, a stale panel, something appearing in the wrong chat. Reproduces the symptom headlessly in the web client to decide whether it's a Limb rendering bug or the Brain sending bad state, before anyone reads Python.
---

# Reproducing a GUI symptom without running Chad

Matthew reports these from the **desktop GUI**, but the desktop Limb and the web client
render from the same React code. So a described symptom can usually be reproduced in
jsdom with synthetic state — no Brain, no model, no LM Studio, deterministic, seconds
per iteration.

The point is not to fix the bug first. It is to **localise** it:

| Reproduces headlessly? | Meaning | Where to work |
|---|---|---|
| Yes | The Limb renders wrong given that state | `src/clients/web/src` |
| No | The Limb is fine; Chad sent odd state | Python: orchestration / approval_store / emitters |

That split is most of the debugging, and it costs one test file.

## Step 1 — decide which layer drives the symptom

Two different render paths. Picking wrong wastes the most time here.

- **Streaming turn content** — thinking text, tool cards, plan cards, the answer bubble,
  anything that flickers/truncates/duplicates *during* a turn. Driven by the typed stream
  protocol. Use the frame-replay harness: `src/clients/web/src/testkit/domReplay.tsx`,
  `replayTurnInDomTyped(frames)`. See `src/clients/web/src/testkit/README.md`.
- **Message/history state** — clarifying-question cards, approval panels, artifacts,
  sources footers, anything attached to a committed `ChatMessage`. These are NOT in the
  stream protocol. Drive them by setting `chatMessages` in the store and rendering
  `<ChatStream/>` directly. Model: `src/clients/web/src/testkit/clarifyingFollowUp.dom.test.tsx`.

If unsure: grep for the component in `ChatStream.tsx` and look at whether it reads from
`renderEvents`/`streamTurn` (streaming) or from `msg.*` (message state).

## Step 2 — MUST stamp conversation ids or nothing renders

The single biggest trap. Live-turn rendering is gated by
`ownsActiveConversation(streamConversationId, activeConversationId)` in
`src/session/sessionGuard.ts`, which returns **false when either id is null**. A harness
that leaves them null mounts the component, folds every event correctly, and renders
**nothing** — content assertions read `''` and only "length never regressed" style
assertions pass, vacuously.

This silently killed the replay harness for months (10 tests green-looking on empty DOM).
Always set both to the same concrete id:

```ts
useAppStore.setState({
    activeConversationId: REPLAY_CONVERSATION_ID,
    streamConversationId: REPLAY_CONVERSATION_ID,
});
```

`clearThinkingEvents()` nulls `streamConversationId`, so stamp the ids **after** calling it.

If a new repro renders empty, check this before believing the component is broken.

## Step 3 — write the smallest sequence that should be fine

Encode the user's description as message/frame state, and assert the CORRECT behaviour
(not the buggy one). Always include a baseline case that should pass, so a repro that
"fails" for a setup reason is distinguishable from a real one.

```bash
cd src/clients/web && npx vitest run src/testkit/<yourRepro>.dom.test.tsx --reporter=basic
```

Expect to be wrong once or twice. A hypothesis that does NOT reproduce is a real result —
it eliminates a mechanism. Keep those cases in the file with a comment saying they were
checked and did not reproduce; they stop the next person re-testing the same idea.

## Step 4 — act on the outcome

- **Reproduced** → fix in the web client, keep the repro as a regression test, and say
  plainly that it was a Limb bug.
- **Not reproduced** → the render is correct for that state, so the state is wrong. Move
  to the Python side and capture what Chad actually sent.

### Capturing a real fixture

Dev builds arm the recorder automatically in `main.tsx`, so capture is already running
before the bug happens (starting it afterwards is how you lose the interesting frames).
Drive the scenario in the GUI, then in devtools:

```js
window.chadFrameCount()                        // confirm frames are accumulating
window.chadDownloadFrames('turn_my_bug.jsonl') // save it
```

Drop the file in `src/clients/web/src/testkit/fixtures/` and add a `describe` block in
`turnDomReplayTyped.dom.test.tsx`.

Recordings capture the **typed** seam (`window.applyStreamEvent`) — the events that
actually drive rendering — and are replayed verbatim. Older hand-authored fixtures only
have the legacy `thinking` channel and get reconstructed through `legacyFrameBridge`,
which is a lossy mirror. Prefer a real capture whenever the difference could matter.

## Verify before claiming it's fixed

```bash
cd src/clients/web && npx vitest run --reporter=basic && npm run build
```

Both must be clean. These are pure frontend (node + jsdom) — **no LM Studio**, so they're
safe to run while Matthew is using the box. The Python suite is NOT: never run
`pytest tests/unit/` without asking.

## Worked example

"Clarifying questions look weird, and when I send a follow-up I see the same questions
again." Localised in one file: `ChatStream.tsx` inferred the card from mere adjacency to
a resolved questions message, so any next message inherited it. The first hypothesis
(in-progress AI messages being filtered, changing adjacency) did **not** reproduce and was
kept as a documented negative case; the second — card resolved in place with no separate
answer message — did. Fix: require the message text to match the recorded answer.
