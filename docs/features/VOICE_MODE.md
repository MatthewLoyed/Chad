# Voice Mode (discrete, turn-based)

**Voice mode** is a hands-free, one-shot command pipeline:

```
[always armed] "Hey Chad"  (openWakeWord wake word, local/free/no account)
   -> earcon beep + buddy "listening" pose
   -> record one utterance (ended by silence)
   -> faster-whisper STT   (local, offline) -> text
   -> normal Chad text turn (transcript over WebSocket -> headless Brain),
      speaking each sentence via the TTS engine as it streams in and racing a
      filler-phrase timeout against the first one
   -> re-arm               (say "Hey Chad" again for the next command)
```

> **TTS engine:** the default is now **Orpheus** (`tts.engine`, served by LM Studio
> on :1234) with **Kokoro** as the automatic fallback (`tts.fallback_engine`).
> Older revisions of this doc described Kokoro as the only engine; references to
> "Kokoro" below mean "the configured TTS engine" unless stated otherwise.

> Epic [#291](https://github.com/MatthewLoyed/AIAssistant/issues/291) tracks turning
> this into a near-instant, context-aware "live voice" experience. Phase 1
> (non-blocking filler), Phase 2 (sentence-chunked TTS streaming), and Phase 3
> (dedicated fast voice lane + narrated model handoff + dynamic filler, this doc)
> are done; later phases add barge-in and a full-duplex spike — see issues
> #294-#295.

It is **distinct from "live voice mode"** (the Gemini Live native-audio session,
`session.default_voice_mode`). Voice mode is fully local except the LLM turn
itself, so it is free/offline-friendly.

## Where it runs (important)

The **Brain is headless and has no audio** — `RemoteFactory` builds the server's
`SessionManager` with `MockAudio`. The microphone, speaker, wake word, TTS, and
the desktop buddy all live on the **Limb** (the desktop client, `main_gui.py`),
which already plays TTS for the headless Brain. So **voice mode runs in the Limb**
and reaches the Brain the same way a typed message does — over the WebSocket.

To use it you run **both**: the Brain (`server.py`) and the Limb (`main_gui.py`).

> History: an earlier draft wired this into `ChadAssistant`/`BootManager`, which
> turned out to be dead pre-split code the running system never executes. Those
> were removed; voice mode now lives on the live Limb path.

## Architecture (decoupled)

`VoiceModeController` (`src/core/orchestration/voice_mode_controller.py`) is the
orchestration-layer loop and depends only on injected callables/interfaces — no
hard dependency on any wake word engine, audio hardware, the STT engine, or the Brain.

`VoiceModeHost` (`src/clients/desktop/voice_mode_host.py`) is the **only** wiring
point; it injects the concrete collaborators:

| Collaborator | Type | Wired by `VoiceModeHost` to |
|---|---|---|
| `wait_for_wake` | `async () -> bool` | `WakeWordDetector` (openWakeWord by default) over the mic broadcast |
| `recorder` | `async record() -> bytes` | `UtteranceRecorder` (silence end-pointing) |
| `stt_engine` | `BaseSTTEngine` | `FasterWhisperSTTEngine` via `get_stt_engine()` |
| `run_turn` | `async (text, on_sentence) -> str` | `GUIChadLimb.run_voice_turn` (WebSocket → Brain) |
| `speak` | `async (text) -> None` | `get_tts_player().speak` (Kokoro) |
| `on_listening` | `() -> None` | synthesized earcon beep |
| `on_state` | `(str) -> None` | window status + buddy `VOICE_STATE` signal |

`run_voice_turn` correlates the asynchronous `AI_RESPONSE` back to the caller by
`turn_id` (a future), so the controller can speak the Brain's reply. Streaming
`AI_RESPONSE` fragments (`fragment: true`, the same channel the GUI chat uses for
token deltas) are fed through a `SentenceAccumulator`
(`src/core/tts/sentence_accumulator.py`) that buffers deltas and yields complete
sentences as they close; each one is spoken immediately via the controller's
`on_sentence` callback instead of waiting for the final reply. Any trailing
partial sentence is flushed and spoken when the final (non-fragment)
`AI_RESPONSE` arrives.

**Wake word (#649).** `WakeWordDetector` (`src/core/audio/wake_word.py`) uses
openWakeWord: free, open source, ONNX-on-CPU, no API key and no vendor account.
It replaced Picovoice Porcupine, which is a dead end for this project — that
account was deleted and signup now requires a company email. openWakeWord
ships pretrained models (`hey_jarvis` is the interim default); a custom
"Hey Chad" `.onnx` trained via openWakeWord's Colab notebook can be dropped in
`src/assets/` and pointed at via `wake_word.keyword_path`. It expects 16kHz
mono int16 frames, which is exactly what `AudioInput` already broadcasts — no
resampling needed.

Modules:
- `src/core/stt/` — `BaseSTTEngine`, `engines/faster_whisper_engine.py`, `stt_factory.py`
- `src/core/audio/wake_word.py` — openWakeWord-backed wake word detector
- `src/core/audio/utterance_recorder.py` — silence-end-pointed capture
- `src/core/tts/sentence_accumulator.py` — streams deltas into completed sentences
- `src/core/tts/dynamic_filler.py` — query-tailored filler generation (Phase 3)
- `src/core/orchestration/voice_mode_controller.py` — the loop
- `src/core/orchestration/nodes/voice_node.py` — the fast voice-lane LangGraph node
- `src/tools/system/escalate_to_agent.py` — the fast→heavy model handoff tool
- `src/clients/desktop/voice_mode_host.py` — Limb-side wiring

## Phase 3: dedicated fast voice lane + narrated model handoff

> **Two bugs here were only found by probing a live Brain (2026-07-25); both made
> the fast lane silently inert while the logs still looked half-correct.**
>
> 1. **The lane never ran.** `TurnCoordinator` re-derived the turn category from
>    the resolved `turn_mode` after intent resolution. `turn_mode` only spans
>    chat/agent/plan, so `category="voice"` was rewritten to `"agent"` right
>    before the graph state was built and `route_entry` never reached
>    `voice_responder` — every spoken turn ran the full supervisor + tool
>    selection pipeline (22-38s to first speech). The earlier steps that read the
>    *incoming* category (9B voice model selection, `thinking_level="minimal"`)
>    still fired, which is what made it look wired up. Categories that name a
>    deliberately chosen lane now survive intent resolution via
>    `TurnModeResolver.category_for_turn` (`EXPLICIT_LANE_CATEGORIES`).
> 2. **The escalate tool was usually unbound.** `voice_node` built its tool list
>    by intersecting the hybrid RAG shortlist with `VOICE_TOOL_NAMES`. The
>    shortlist is query-driven, so "check my email inbox" surfaced Gmail tools and
>    no `escalate_to_agent` — leaving the model with zero tools bound while the
>    prompt still told it to escalate, so it fabricated an inbox listing instead
>    of handing off. The voice tool set is now bound unconditionally from the
>    registry, matching what this doc always claimed.

Every voice turn (`category="voice"`) first passes through a **three-tier quick
path** in `TurnPipeline` (`turn/pipeline.py`) before the LangGraph voice node:

1. **Tier 1 — `voice_fast_lane`** (no LLM): regex (+ embedding fallback) for
   open/launch/go-to. Executes `open_browser` and returns `"Opening X."` with
   `stop_reason=fast_lane`. Polite wrappers (`can you open…`, trailing `please`)
   are stripped so STT phrasing still hits.
2. **Tier 2 — `voice_quick_tool`** (resident 9B, minimal prompt + one-step tools):
   `open_browser` / `open_app` / `web_search` / `get_datetime` / `search_gmail` /
   `hubspace_control`. Search + Gmail run a short synthesis step so the user
   hears an answer (`stop_reason=quick_tool_lane`). Multi-step / calendar /
   draft-send still return NONE → tier 3.
3. **Tier 3 — `voice_responder`** (`voice_node.py`): single-hop LangGraph node on
   `local_models.category_models.voice` (default `qwen/qwen3.5-9b`) with
   `hubspace_control` + `escalate_to_agent` only. Fallback when tier 2 declines;
   real multi-step work escalates to Gemma agent.

**Model handoff.** When tier 3 can't handle a request (multi-step
tasks, full email threads, Canvas, files, coding, research — not simple
open/search/time/lights/inbox check),
it calls `escalate_to_agent`
(`src/tools/system/escalate_to_agent.py`). `TurnCoordinator` detects the
`voice_escalate` flag and re-dispatches the same utterance on `category="agent"`
(the heavy model + full hybrid RAG/tool selection). This re-dispatch is
synchronous — it does not run concurrently with the fast lane — but two things
keep the wait from feeling frozen:

1. The voice prompt asks the model to say a short acknowledgment
   ("Let me check on that") *before* calling `escalate_to_agent` or
   `hubspace_control`, in the same message. `voice_responder` tags its chain
   invocation `voice-llm` so `LangGraphEngine`'s streaming loop forwards that
   text live, the same way `conversational-llm`/`supervisor-llm` do — this was
   a fixed bug: the tag was missing entirely, so voice replies (including any
   pre-tool narration) never streamed and only reached the user as one final
   blob at turn end.
2. While the heavy agent-lane pipeline runs, longer-wait status labels may be
   spoken as narration. Early GUI processing strings ("Let me think…",
   "Processing…") are **not** emitted before tier-1/2 and are blocked from
   voice TTS if they arrive — those were false canned fillers on instant opens.

True concurrent execution (accepting new mic input, or the fast lane
continuing to chat, while the heavy pipeline runs in the background) is
explicitly deferred — it would require bypassing the per-session turn lock in
`ConversationOrchestrator` and is real architectural surgery, not needed to
get the "Chad keeps talking instead of going silent" feel.

**Fillers (template-first).** First speech must be instant and contextual
without an LLM round-trip (local model floor is several seconds — a ~500ms
"dynamic filler" race always lost). Behavior:

- **Instant open (tier-1 regex):** no filler race at all — first speech is
  `"Opening X."`
- **Other waits:** cheap intent templates (`"Looking that up."`, etc.) beat the
  generic canned rotation; optional LLM dynamic filler
  (`src/core/tts/dynamic_filler.py`) may speak as a **mid-wait follow-up** only
  (`voice_mode.dynamic_filler` / `dynamic_filler_wait`).

Post-wake routing (inject transcript, no mic) is probed by
`python -m evals.voice_front_desk_probe`.

## GUI feedback (issue #654)

A voice turn is visible in the main window. `VoiceModeController` emits a
`VoiceTurnState` on every phase transition, which the Limb pushes straight to
the React app:

```
VoiceModeController._notify(phase, transcript=…, reply=…, error=…)
  → VoiceModeHost._on_state
  → MainWindow.push_voice_state          (evaluate_js)
  → window.handleVoiceState              (streamRuntime.ts)
  → store.applyVoiceState → voiceTurn slice
  → VoiceOverlay: ambient glow + top pill
```

Phases (`src/shared/voice_state.py`, mirrored as `VoicePhase` in `store.ts`):
`armed → listening → transcribing → heard → thinking → speaking`, plus `error`.
`heard` carries the STT transcript, which the pill shows verbatim — that's the
fastest way to tell a mis-transcription apart from a routing failure. Silence,
an empty transcript, and a Brain that returns nothing all emit `error` with a
reason rather than re-arming in silence.

**This is a dedicated channel on purpose.** Voice state used to ride
`window.update_status` → `handleStateUpdate`, which is shared with the chat turn
lifecycle; `brainSocket` pushes `handleStateUpdate('ready')` at several turn-end
points, so unrelated chat activity wiped it. `voiceTurn` is its own store slice
and cannot be clobbered.

The overlay is deliberately **not** wired into the chat: a "Hey Chad" turn is a
quick command, not a conversation, so it never writes to the active chat's
message list. Click the pill to expand it and see the transcript and reply.

### Availability

`VoiceModeHost.start()` publishes a `VoiceAvailability` on every outcome, so all
failure paths (`audio_init_failed`, `mic_unavailable`, `wake_word_unavailable`,
`stt_unavailable`) surface a message and a remedy in the GUI instead of only a
log line. Genuine failure paths appear as a transient, dismissible chip in the
main window (auto-dismissing after 8 seconds and offset from the sidebar rail),
while the intentional `disabled` state avoids cluttering the main window with a
banner. Both are reflected as a live status line under Settings → Audio & Voice,
which also has the on/off toggle (`get_wake_word_mode` / `set_wake_word_mode`). Both are
desktop-only — the mic and wake word engine live in the Limb, so the browser/PWA
client hides the section entirely.

## Buddy feedback

On wake, the Limb sends a `VOICE_STATE` message to the Brain; `BuddyBridge` sets a
`listening` state and pushes it to the desktop buddy (chad-hud) over the
`/buddy/stream` SSE. It clears when the turn starts, so the normal
thinking/working animation takes over. Only changes are sent, since phases now
fire more often than the send needs. There's also an audible earcon. In the main
window the in-app avatar is driven from the voice phase directly
(`resolveBuddyMode`), because a voice turn belongs to no conversation and so is
invisible to the conversation-scoped stream state.

## Enabling it

1. Install deps (adds faster-whisper + openwakeword):
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
   ```
2. Nothing else needed for wake word — no API key or account. openWakeWord
   downloads its pretrained model automatically on first use.
3. Flip the flag in `config/config.yaml`:
   ```yaml
   voice_mode:
     enabled: true
   ```
4. Verify before going live:
   ```powershell
   $env:PYTHONPATH="."; .\.venv\Scripts\python.exe tests/manual/verify_voice_mode.py
   ```
5. Run the Brain **and** the Limb:
   ```powershell
   $env:PYTHONPATH="."; .\.venv\Scripts\python.exe src\remote\server.py
   $env:CHAD_UI_DEV="true"; $env:PYTHONPATH="."; .\.venv\Scripts\python main_gui.py
   ```

## Configuration

```yaml
tts:
  engine: orpheus          # orpheus | kokoro | gemini
  fallback_engine: kokoro  # used when the primary yields no audio (falsy = disabled)
  voice: leo               # ORPHEUS voice: tara|leah|jess|leo|dan|mia|zac|zoe
  kokoro_voice: am_michael # KOKORO voice: af_*/am_* — NOT interchangeable with the above
  gemini_voice: Puck       # Gemini voice
```

Voice names live in per-engine namespaces. Pointing Kokoro at an Orpheus voice
(`leo`) 404s the voice download and yields **zero audio with no playback error**,
which is why the fallback engine appeared to work until it was actually needed.

```yaml
wake_word:
  keyword: hey_jarvis      # openWakeWord pretrained model name (interim until "Hey Chad" is trained)
  keyword_path:            # custom trained .onnx; overrides `keyword` above
  threshold: 0.65          # detection confidence threshold (0-1, higher = fewer false positives)
```

```yaml
stt:
  engine: faster_whisper   # local CTranslate2 Whisper
  model: small             # tiny|base|small|medium|large-v3
  device: cpu              # cpu | cuda
  compute_type: int8       # int8 (CPU) | float16 (GPU)
  language: en
  beam_size: 1
  vad_filter: true

voice_mode:
  enabled: false
  silence_timeout: 1.2       # trailing silence (s) that ends a command
  max_utterance_seconds: 15
  start_timeout: 6.0         # give up if no speech after the earcon
  vad_threshold: 0.01
  earcon: true
  filler_timeout: 0.6        # seconds to wait for run_turn before speaking a filler; <= 0 disables
  filler_phrases:            # canned rotation used as a fallback (see dynamic_filler)
  - "Let me think..."
  - "One sec..."
  - "Working on it..."
  - "Give me a moment..."
  dynamic_filler: true       # generate a query-tailored filler via the resident fast
                             # voice model instead of the canned rotation (Phase 3)
```

## Graceful degradation

If faster-whisper or openwakeword is not installed, the configured wake word
model is missing/unloadable, or audio hardware is unavailable, the host logs a
warning and no-ops — it never crashes the Limb. The flag defaults to `false`,
so behavior is unchanged until you opt in. Each of these now also publishes a
`VoiceAvailability` reason to the GUI (see "GUI feedback" above), so a silent
no-op is visible rather than indistinguishable from a working setup.

## Troubleshooting

**`WakeWordError: openwakeword is not installed` on startup.** Install the
optional dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
```

**Wake fires repeatedly / earcon beeps in a loop.** Fixed in #654; if it comes
back, these are the two mechanisms that caused it.

1. *The model's feature window.* openWakeWord scores a rolling ~1.5s window of
   audio features. `WakeWordDetector.reset()` (which calls the library's
   `Model.reset()`) now runs on every detection, so the wake phrase is not still
   sitting in that window when the loop re-arms. Without it, one "Hey Chad"
   scores above threshold repeatedly.
2. *The earcon being heard as your command.* `SoundManager.play_sound` plays on a
   background thread, so the activation sound is still audible once
   `UtteranceRecorder.record()` starts, and at `voice_mode.vad_threshold` (0.01
   RMS) it reads as speech. The recorder would start on the beep, end-point on
   the silence after it, and hand STT a clip with no command in it — which
   transcribed to nothing and re-armed, beeping again.

   `voice_mode.earcon_guard_seconds` (default 0.4) discards audio for that long
   before listening for speech at all. **It must be >= the activation sound's
   length.** The default `activation_short.wav` is 0.32s. The previous default,
   `mixkit-interface-hint-notification-911.wav`, is a 1.7s sustained chime — far
   longer than the guard and longer than a wake confirmation should be. If you
   change `sounds.sound_urls.activation`, measure the new file and raise the
   guard to match, or the loop returns.

   Note the guard is dead air: you cannot be heard during it. That is the reason
   to keep the earcon short rather than raise the guard.

**Wake word never fires / fires constantly.** Adjust `wake_word.threshold`
(default `0.5`) — lower it if "Hey Chad" isn't registering, raise it if normal
conversation triggers false positives. The pretrained `hey_jarvis` model is an
interim stand-in (see #649); accuracy on the real "Hey Chad" phrase improves
once a custom `.onnx` is trained via openWakeWord's Colab notebook and pointed
at via `wake_word.keyword_path`.

**No speech is captured / "Invalid device".** Windows can be left with *no default
recording device* (`sd.default.device` reports `-1` for input) even when mics are
present. `AudioInput` now auto-selects the first input-capable device and logs a
warning, but which mic you get is then arbitrary — set a default input device in
Windows Sound settings to pick deliberately. Check the current state with:

```powershell
.\.venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.default.device); print(sd.query_devices())"
```

Note the default *output* device matters too: TTS plays to it, so if it points at
an HDMI monitor with no speakers you will see the pipeline succeed and hear
nothing.

## Tests

- `tests/unit/test_voice_mode_controller.py` — pipeline + recorder + filler/streaming races + dynamic-filler fallback + phase notifications (transcript carry-through, error phases), fakes only (CI-safe).
- `src/clients/web/src/store.voice.test.ts` — voiceTurn/voiceAvailability slices, including that voice state survives a chat turn ending.
- `src/clients/web/src/components/common/chad-avatar/resolveBuddyMode.test.ts` — voice phase → buddy mode mapping.
- `tests/unit/test_sentence_accumulator.py` — delta buffering / sentence-boundary detection.
- `tests/unit/test_turn_delivery.py` — `GUIChadLimb` fragment/status routing into `on_sentence`, voice-vs-GUI delivery gating.
- `tests/unit/test_voice_node.py` — `voice_responder` tagging (streaming) and fixed tool-set binding.
- `tests/unit/test_langgraph_voice_streaming.py` — `voice-llm`-tagged chunks reach `on_token`.
- `tests/unit/test_buddy_bridge.py` — includes the `listening` state.
- `tests/unit/test_voice_lane_routing.py` — category survives intent resolution; `route_entry` reaches `voice_responder`.
- `tests/unit/test_tts_fallback.py` — primary→fallback engine handoff + per-engine voice resolution.
- `tests/unit/test_wake_word.py` — openWakeWord threshold/buffering + engine-selection facade, fakes only (CI-safe, no ONNX runtime or vendor account).
- `tests/unit/test_audio_input_device.py` — input-device pick when Windows has no default mic.
- `tests/manual/verify_voice_mode.py` — interactive mic + model check (not in CI).
- `tests/manual/probe_audio_roundtrip.py` — **mic-less** TTS→PCM→STT round trip; verifies both real engines without hardware or a human.
- `tests/manual/probe_voice_turn.py` — live latency/escalate-to-agent benchmark against a running Brain (`--port=8790` to target a dev Brain; defaults to prod 8767).
