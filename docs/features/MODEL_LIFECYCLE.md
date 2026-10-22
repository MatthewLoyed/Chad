# Model Lifecycle Coordinator

Every LM Studio load/unload decision in Chad goes through one module:
[`src/core/intelligence/model_lifecycle.py`](../../src/core/intelligence/model_lifecycle.py).

## Why this exists

Before this consolidation, at least six independent code paths each talked to LM Studio's
load/unload API with their own private implementation and no coordination:

- `LLMToolSelector` (tool selector, 9B) — its own sync `urllib` load/unload/poll loop.
- `IntentModeRouter` — a near-identical second private implementation.
- `ModelRouter._warmup_lmstudio` / `_ensure_local_model` — a third implementation, plus a
  hard `raise` if the main model wasn't found resident (no attempt to load it first).
- `scout_node._maybe_unload_main_model` — fire-and-forget unload of the main model.
- `handoffs._swap_for_subagent`/`_swap_back` — fire-and-forget unload/reload around subagent
  delegation.
- `turn_coordinator._preload_main_model` — loads the main model before every supervisor call.
- `executor_strategy` browser vision-assist — JIT-loads a vision model on a hardcoded
  "these are small enough to coexist" assumption.

None of these shared a lock, a registry of what's currently wanted, or a VRAM budget, so two
lanes could race the same decision — which is exactly LM Studio's own documented failure mode:
concurrent JIT load requests for the same model can spin up **duplicate instances** and OOM the
box (see the `lmstudio-heavy-model-double-load` incident). Auto/voice turns stack several of
these lanes in one turn (intent router → tool selector → main-model preload → possible subagent
swap), so they hit the race far more often than a plain chat turn.

## Convention

**New code must never call `model_swap.py` directly, and must never hand-roll another
load/unload implementation.** Always go through `ensure_resident` / `release` (or their
`*_sync` counterparts) in `model_lifecycle.py`.

## API

```python
from src.core.intelligence.model_lifecycle import ensure_resident, release, OWNER_MAIN_MODEL

# Async callers (turn_coordinator, handoffs, scout_node, executor_strategy):
ok = await ensure_resident(base_url, model_id, owner="my_lane")
await release(base_url, model_id, owner="my_lane")

# Sync callers (llm_selector.py, intent_mode_router.py, model_router.py):
from src.core.intelligence.model_lifecycle import ensure_resident_sync, release_sync
ok = ensure_resident_sync(base_url, model_id, owner="my_lane")
release_sync(base_url, model_id, owner="my_lane")
```

- **`owner`** is an arbitrary tag identifying the caller. Claiming the same model with the same
  owner repeatedly is idempotent — it never double-loads. `release` only actually unloads once
  every owner that claimed a model has released it.
- **`OWNER_MAIN_MODEL`** is the shared tag every site that manages the supervisor's main chat
  model uses (`turn_coordinator`'s preload, `handoffs`' subagent swap, `scout_node`'s secondary-
  model gather) — using the same tag lets them coordinate through one wanted-set entry instead
  of guessing at each other's intent.
- **`priority="background"`** (heartbeat/cron/autonomous callers) waits for the active
  interactive turn to go idle before evicting/loading, instead of fighting a live chat for VRAM.
  See "Priority queue" below.
- **`context_length`** forwards an explicit JIT-load context length (needed by the tool selector
  to avoid LM Studio's saved-default context rejecting a large catalog prompt).

## How it works internally

- One dedicated background event loop owns all coordination state (per-base_url `asyncio.Condition`
  + a `wanted: Dict[model_id, Set[owner]]` registry + last-touch timestamps for LRU). Both async
  and sync callers schedule onto this single loop via `run_coroutine_threadsafe`, so state stays
  consistent regardless of which thread/loop the caller lives on.
- Every load/unload decision for a given base_url is serialized behind that base_url's condition
  — two lanes can never race the same decision.
- Before loading a model that isn't already resident, a best-effort VRAM budget check
  (`local_models.lifecycle.vram_budget_gb` + `model_size_estimates_gb`, substring-matched against
  the model id) decides whether it fits alongside everything currently claimed resident. If not,
  it evicts unwanted (zero-owner) resident models first, LRU order; only as a last resort does it
  evict a still-wanted model (logged as a warning) — the incoming load always wins rather than
  blocking, matching the codebase's "let JIT own the reload" philosophy.
- Setting `vram_budget_gb <= 0` disables budget-aware eviction entirely (falls back to LM
  Studio's own auto-evict).

## Priority queue (background vs. interactive)

Autonomous callers (heartbeat, cron, scheduled routines) pass `priority="background"` so they
queue behind an active interactive turn instead of evicting a model out from under a live
conversation. "Interactive active" is a **self-decaying signal**, not a sticky flag: every
`priority="interactive"` call (the default — turns, tool selection, intent routing, subagent
swap) refreshes a `last_interactive_touch` timestamp; a background call waits while
`now - last_interactive_touch < interactive_active_window_s` (default 20s), up to
`background_queue_timeout_s` (default 120s), then proceeds anyway with a warning rather than
deadlocking a heartbeat run forever.

It's deliberately not tied to explicit turn-begin/turn-end events — most interactive owners
(e.g. `OWNER_MAIN_MODEL`) never call `release()` at all, so a persistent "is interactive active"
boolean would starve background callers indefinitely. The decaying-touch approach means
background work waits while the user is actively chatting and proceeds once things go quiet.

**Fixed bug (#510):** the wait loop used to call `condition.wait(timeout=remaining)` where
`remaining` was time left until the FULL `background_queue_timeout_s` deadline, not the
`interactive_active_window_s` window — since nothing else touches the condition variable during
an isolated background turn, `notify_all()` never fires, so it slept the *entire* 120s deadline
in one shot instead of re-checking every `interactive_active_window_s` seconds whether the touch
had actually decayed. A single stale `last_interactive_touch` (e.g. from a connect-time
preload) made an isolated background load wait out the whole queue timeout for nothing. Fixed
by polling at `min(window - age, remaining)` intervals so a quiet LM Studio is detected within
`interactive_active_window_s`, not only when something else happens to notify it awake.

**How a turn is tagged `background`:** `current_priority()` reads a dedicated contextvar,
`turn_priority_var` (`src/core/orchestration/context.py`) — NOT `turn_category_var`.
`turn_category_var` gets overwritten mid-turn by `TurnModeResolver` (the raw incoming category,
e.g. `"autonomous"`, is replaced with whichever `chat`/`agent`/`plan` lane the intent router
resolves to), so reading it directly would silently lose the autonomous marker before any
tool-selection or model-preload call ever runs. `turn_priority_var` is set exactly once, from
`_is_background_turn(category, context.platform)` (`turn_coordinator.py`), at the very top of
`turn_coordinator._execute_turn_internal_wrapped` — before mode resolution can touch it — and
every model-lifecycle call site (`llm_selector.py`, `intent_mode_router.py`, `model_router.py`,
`turn_coordinator.py`, `handoffs.py`, `executor_strategy.py`) calls `current_priority()` at its
own call site to read it back. Heartbeat/cron/webhook turns (`category="autonomous"`, set in
`heartbeat_service.py`/`cron_scheduler.py`/`gmail_handler.py`) also skip the intent-router
classification entirely now (treated like `system_turn`) — no reason to spend a gemma-1b
call/load classifying a turn whose purpose is already known.

**Evals are a separate mode from live user turns (`platform="eval"`, #632).**
`evals/dry_run.py` and `evals/live.py` both tag turns `platform="eval"`
(`RequestContext.platform` in-process; WebSocket `"platform"` field for live/
workflow — real Limb clients never send this and default to `"gui"`). That tag
is **not** background:

| Policy | Live user (`gui`) | Eval (`platform=eval`) |
|---|---|---|
| Model-lifecycle priority | interactive | interactive (same load path) |
| Who yields when both want VRAM | N/A (user owns the box) | **Eval wins** — `eval_exclusivity` blocks non-eval turns for the duration |
| Unload 9B after tool select | yes (keep main model fast) | **no** (keep selector hot across cases) |
| Post-turn `schedule_warm` of main chat model | yes | skipped (`_is_eval_turn`) |
| Live/workflow case concurrency | N/A | **sequential by design** (subagents / VRAM) |
| Dry-run tool-selection concurrency | N/A | parallel workers OK (~6) |

So an eval suite mid-flight does **not** queue behind your desktop chat and does
**not** get evicted by it — desktop/cron wait until the eval turns finish. See
`src/core/orchestration/eval_exclusivity.py` (canonical policy) and `evals/AGENTS.md`.

## `lmstudio-gate` and `max_concurrent` — a SEPARATE serialization point (#510)

The Windows alpha defaults `local_models.gate.enabled` to **false** and sends
inference directly to the selected backend. Keep this optional queue off in a
frozen install: its source-only `python -m` launcher otherwise re-enters the
Brain executable instead of starting the sidecar. Source-checkout developers
may opt in when sharing inference with an eval process. The behavior below
describes that opt-in configuration, not the alpha default.

For an OpenAI-compatible custom endpoint with no API key, the adapter supplies a
public non-secret SDK sentinel. A user-supplied key takes precedence; cloud
requests without a custom endpoint still require their actual credentials.

`lmstudio-gate` (`src/infrastructure/llm/lmstudio_gate.py`) is a standalone cross-process
priority-queue sidecar in front of LM Studio (issue #478) — every `LiteLLMAdapter.generate()`
call for a local model gets routed through it (`_apply_gate_routing`) instead of hitting LM
Studio directly. **This is a completely separate serialization point from everything above in
this doc** — the in-process `model_lifecycle.py` coordinator only governs load/unload/eviction
decisions; the gate governs how many actual inference REQUESTS are allowed to run at once.

Its `PriorityGate.max_concurrent` (`local_models.gate.max_concurrent` in config.yaml) defaults
to a value that, if left at `1`, silently serializes **every** local-model inference call to
strict single-flight — including `parallel_research`'s whole N-worker fan-out. Since
`asyncio.gather` just queues N requests behind that one gate slot, a fan-out can look identical
to a serial loop from the outside even though the app code genuinely fired them concurrently.
This was invisible to `evals/parallel_probe.py` because that probe hits LM Studio's port
directly (bypassing the gate entirely) to isolate the "does LM Studio itself batch concurrent
requests" question — it does — so the probe looked fine while that gate-enabled setup
didn't. If parallel worker fan-out looks non-concurrent with the gate enabled but IS
concurrent when probed directly against LM Studio, check `local_models.gate.max_concurrent`
before suspecting LM Studio or the app's own async code. Current default: `6`, matching the
measured real LM Studio concurrency ceiling (`parallel_probe.py --sweep`) — high enough for
genuine same-model worker fan-out, while still bounding runaway cross-process contention (the
gate's original purpose, #478).

## Config (`config/config.yaml`, under `local_models.lifecycle`)

| Key | Default | Meaning |
|---|---|---|
| `vram_budget_gb` | `16` | Total VRAM budget; `<= 0` disables budget-aware eviction. |
| `model_size_estimates_gb` | see config | Substring → GB size table for the budget check. |
| `pinned_models` | `[]` | Substrings of models that never evict **each other**. See below. |
| `background_queue_timeout_s` | `120` | Max wait for a background-priority claim. |
| `interactive_active_window_s` | `20` | How long an interactive claim keeps background callers queued. |

Also relevant (`config/config.yaml`, `tools.*`):

| Key | Default | Meaning |
|---|---|---|
| `wait_narrator_enabled` | `false` | Cosmetic GUI status-line filler. Off — see below. |
| `llm_selector_unload_after_select` | `true` | Unload the selector right after tool pick (relies on `OWNER_TOOL_SELECTOR`, below). |

### `pinned_models`: don't swap at all on a single-GPU box

The "swap during a turn, coexist at rest" design below keeps both models hot *between*
messages but still swaps them *within* one — the selector's own budget-enforced load evicts
the 26B supervisor, and the end-of-turn warm reloads it. On a 16GB card that is a **~15.6GB
model streamed off disk on every single message**, and the sustained I/O + VRAM
reallocation stalls the whole desktop — which matters a lot when Chad is meant to sit in the
background all day while you're doing something else.

`local_models.lifecycle.pinned_models` removes the swap entirely. Pinned models never evict
one another, so the supervisor and the selector simply both stay loaded; LM Studio spills
whatever exceeds VRAM onto CPU RAM instead of failing (the same trade `warm_models` already
relies on). In steady state a normal turn performs **zero loads and zero unloads**.

```yaml
# config/preferences.yaml
local_models:
  lifecycle:
    vram_budget_gb: 16
    pinned_models: ["gemma-4-26b", "qwen3.5-9b"]
```

Two deliberate limits:

- **The pin is not absolute.** A non-pinned incoming model (`qwen3.6-35b-a3b` for Chad Code,
  `vl-8b` for the browser agent) still evicts pinned models normally. Those genuinely don't
  fit alongside a 15.6GB model, and honoring the pin there would OOM the box rather than
  protect anything — the pin only suppresses the *routine* supervisor↔selector churn.
- **`llm_selector_unload_after_select` is ignored for a pinned selector**
  (`llm_selector._maybe_unload_after_use`). Unloading it would pay the reload next turn while
  keeping none of the benefit: under the pin, that reload no longer evicts the main model,
  so there is no swap left for the unload to "save".

Cost: the CPU-spilled model runs slower. That's the intended trade — the selector emits a
short JSON tool pick, so a few seconds there beats a 15.6GB reload per message. Empty by
default, so no existing setup changes behavior.

### Escape hatch: `CHAD_PINNED_TOOLS` (eval lanes only, #675)

Environment variable, comma-separated tool names. When set, `TurnToolSelector` binds core
tools plus exactly those and **never calls the 9B selector**. An empty string is meaningful
and distinct from unset: "core tools only".

This exists because on a single-GPU box the selector costs far more than its own latency.
The 9B doesn't fit alongside the 26B supervisor inside `vram_budget_gb`, so every turn
evicts the supervisor and then pays to re-preload it. Measured on the #660 lane:
`tool_9b_ms=41130` on one case and a **22s** supervisor preload on the next — roughly half
the wall clock of a case, spent entirely on swap choreography.

Only sound for a lane that isn't measuring tool selection and whose tool set is known up
front. **Never set it in production**: it disables tool discovery outright.

## LM Studio's Auto-Evict vs. JIT loading (#592)

**The bug that cost a full session to root-cause, and the fix.** Live eval turns that should
take ~10-15s were taking 65-120s, with `Failed to load model "X". Error: Engine protocol
startup was aborted.` / `Model is unloaded.` in `~/.lmstudio/server-logs/`. The eventual cause
had nothing to do with prompts, tool selection, or the model itself — it was three residency
bugs that, together, meant Chad was silently JIT-loading every model instead of using LM
Studio's explicit load API, and LM Studio's own **Auto-Evict** ("Only Keep Last JIT Loaded
Model") kills a JIT-loaded model **the instant a competing JIT load arrives, without waiting
for it to finish loading or generating** — confirmed upstream behavior
([lmstudio-bug-tracker#671](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/671)).
Models loaded via the explicit `POST /api/v1/models/load` API are immune; JIT-loaded ones are
not. That is the entire mechanism — everything else in this section is either "why Chad kept
JIT-loading despite having a coordinator whose whole job is to prevent that" or "how we proved
it and fixed it."

**Reproduced in total isolation** — no Chad, no Brain, just two `httpx` calls against LM Studio
directly — in [`scripts/repro_592_jit_evict.py`](../../scripts/repro_592_jit_evict.py). Start
loading model A, and ~1s later (while A is still loading) fire an inference request for model
B. Explicitly-loaded A survives; JIT-loaded A gets killed with the exact production error
string. Run this FIRST if you ever suspect this class of bug again — it takes under a minute
and needs nothing but a running LM Studio.

### Three bugs that made every load a JIT load

1. **Residency was read from the wrong endpoint.** `LMStudioLifecycle` inherited
   `list_loaded()` from the base class, which hits OpenAI-compat `GET /v1/models`. On LM
   Studio that endpoint lists every **downloaded** model (18, in this case), not the resident
   ones — the identical "downloaded ≠ loaded" trap `ollama.py` already knew to avoid with
   `/api/ps`. Every residency check answered "yes, already loaded," so `load()`'s idempotence
   guard skipped the real load, `_ensure_resident_impl` returned `True` immediately (the whole
   coordinator — claims, `_make_room`, VRAM eviction — went inert), and the wait-narrator's
   "only if already resident" guard always passed. Fixed by overriding `list_loaded()` to read
   the native `GET /api/v1/models`'s `loaded_instances` field instead.
2. **The load payload carried the wrong id.** Callers pass the router/LiteLLM name
   (`openai/google/gemma-4-26b-a4b-qat`); LM Studio answers `model_not_found` for that. The
   fix (`clean_model_id`) was already computed at the call site and simply never used in the
   payload — a one-line bug that made every explicit load silently fail and fall through to
   JIT.
3. **The main-model preload was a guaranteed no-op.** `turn_coordinator._preload_main_model`
   read `base_url`/`model_name` straight off whatever adapter it was given — but the real
   active adapter is often a `LiteLLMModel` wrapper (`models/litellm_model.py`) that keeps the
   actual `LiteLLMAdapter` on `.adapter` and exposes neither attribute itself. Every call
   silently returned `base_url=None` and bailed, so the supervisor model was **never** loaded
   through the coordinator — only ever JIT, on its first inference call. Fixed by
   `_resolve_local_model_target`, which unwraps up to a few levels of `.adapter` looking for
   the first node that actually has both attributes. A previously-silent skip here now logs a
   warning when it fires on a genuinely local turn — a no-op this consequential must not be
   silent.

Two more residency/prefix bugs in the same family, both fixed alongside these: `same_model()`
vs. a raw `==` compare (a prefixed id never equals a bare one, so a "is it already resident?"
check could never be true), and `list_loaded()` returning `[]` for both "nothing resident" and
"the probe failed" — conflating those let `load()` treat an unreachable backend as "nothing
loaded" and risk spinning up a duplicate instance of an already-resident model.

### The wait-narrator: a cosmetic feature that kept re-triggering this bug

`src/core/wait_theater/narrator.py` generates the filler status lines shown in the chat UI
while Chad is "thinking" (e.g. "Checking your calendar…") — purely cosmetic, unrelated to the
actual answer. It opportunistically asks the tool-selector model for 3 lines **only if that
model is already resident**, specifically so it would never itself trigger a load. That guard
is a classic TOCTOU bug: it checks residency, then — after the check returns — fires an actual
HTTP request. In between, the coordinator can (and, once loads became real again, routinely
does) evict that model for the real turn's own use. The narrator's request then JIT-loads it
back, its own 8s timeout fires, LM Studio aborts that JIT load exactly as described above, and
the abort takes the REAL tool-selector call down with it (`Model is unloaded.`) — the single
biggest recurring cause of this issue's latency, hit independently at least three times across
this investigation before it was recognized as one root cause rather than three separate bugs.

**Current state: disabled by default** (`tools.wait_narrator_enabled: false` in
`config/config.yaml`). If it is ever re-enabled, it MUST gate on
`model_lifecycle.active_use_if_resident_sync` (an atomic check-and-pin under the coordinator's
own lock — the model cannot be evicted, and cannot be observed half-loaded, between the check
and the request) rather than a plain "is it loaded?" check. A first attempt at fixing it used
plain `active_use_sync` (marks active but doesn't check-and-pin atomically) — that stopped the
coordinator from evicting the model but did nothing to stop the narrator's OWN request from
JIT-loading a gone one, and because it blocks on the same lock a real load holds, it made the
race window **worse** (~0s → ~10s) instead of closing it. Atomicity is the fix; marking "active"
alone is not.

### Vision: swap during a turn, coexist at rest

Matthew's design, and what's now implemented:

- **At rest** (GUI connect, and again at the end of every turn regardless of how it stopped —
  clean finish, `awaiting_approval`, or `awaiting_input`, since a follow-up in any of those
  cases still runs tool selection) — **both** the main model and the tool-selector model should
  be resident, so the next message never pays a cold load before its first tool-selector call.
- **During an active turn** — whichever model isn't needed for the current step should be freed
  for the one that is: tool selection uses the selector, explicitly unloads it the moment it has
  an answer, then the supervisor gets the freed VRAM to itself. Subagent delegation (the
  existing `handoffs.py` swap, unchanged by this work) follows the same principle.

**The hardware reality this design has to work around: 16GB VRAM total, and the main model
alone (gemma-4-26B, 15.6GB) leaves 0.4GB free.** Both models fully resident is 22.1GB — that
does not fit no matter what the code does. LM Studio's own backend auto-splits the overflow
onto CPU RAM instead of failing (verified: no crash, no OOM, just partial CPU inference for
whichever model doesn't fully fit) — the accepted trade is a bit more latency for the
CPU-spilled model while idle-resident, in exchange for entirely skipping a fresh load. Raising
`vram_budget_gb` does **not** add real VRAM; it only tells the coordinator to stop evicting, and
this trade is only worth it because LM Studio degrades gracefully rather than failing outright.

Two coordinator changes make this possible without disturbing the existing budget-enforced swap
that already worked correctly for the "in-turn" half:

- **`enforce_budget: bool = True`** on `ensure_resident`/`ensure_model_loaded`
  /`_ensure_resident_impl`. Every turn-path caller (the real tool-selector load, the real
  main-model preload) keeps the default — that's the swap, unchanged. Only `warm_models` (used
  by both connect and the end-of-turn hook) passes `enforce_budget=False`, so its own
  back-to-back aux-then-main loads don't evict each other — which is exactly what happened the
  first time this was tried: loading aux, then main, with budget enforcement on, just evicted
  aux again, leaving only one model resident either way regardless of load order.
- **`OWNER_TOOL_SELECTOR`** — a shared owner-tag constant `warm_models` and `llm_selector.py`
  both use for the SAME model. This is the non-obvious part: `release()` only actually unloads
  a model once every owner claiming it has released its claim, and a claim registered under a
  tag nothing ever releases (the old generic `"warm"` tag) sits in `wanted` forever — even
  after the selector's own post-use `release()` fires under its own tag, the lingering `"warm"`
  claim alone would keep it resident permanently, silently defeating the entire "unload right
  after tool selection" behavior with no error or warning anywhere. Sharing the tag means the
  warm claim and the real claim are the SAME set entry, so the real release actually empties it.
  **Lesson: never give a "keep this loaded indefinitely" claim its own private owner tag if
  anything, anywhere, is ever expected to release that model** — use the tag the thing that
  releases it already uses.

Also flipped on alongside this: `tools.llm_selector_unload_after_select` (was `false`) — the
selector already had this exact "unload right after tool pick, before the main model generates"
mechanism built (`llm_selector._maybe_unload_after_use`); it just wasn't wired to fire, and
wasn't safe to enable until the JIT-vs-explicit-load bugs above were fixed (enabling it earlier
would have meant re-loading the selector via JIT on every single turn).

### How this was actually debugged — the general lesson

Every step of this investigation that mattered was a hypothesis tested against a **direct LM
Studio server log read** (`~/.lmstudio/server-logs/<month>/<date>.log`) or a **standalone
reproduction script that bypassed Chad entirely** — never a theory argued from reading Chad's
own code in isolation. Chad's own logs (`[ PERF ]` lines, `[ MODEL_LIFECYCLE ]` lines) tell you
what Chad THINKS happened; only LM Studio's own log tells you what actually happened to the
model process, and the two disagreed at almost every step of this bug (e.g. Chad's code
believed a model was "already loaded" for most of this investigation because of bug #1 above —
nothing in Chad's own logs could have revealed that; only comparing `/v1/models` output against
`/api/v1/models` directly did). When a `--live` eval regresses in duration without any
correctness/judge failure, read the LM Studio log FIRST, before touching prompts or tool
selection code.

## Related docs

- [`LLM_TOOL_SELECTOR.md`](LLM_TOOL_SELECTOR.md) — the tool selector's selection logic (which
  model to target); load/unload mechanics now live here instead.
- [`SUBAGENT_MODEL_SWAP.md`](SUBAGENT_MODEL_SWAP.md) — the subagent handoff swap this module
  now backs.
- [`local-model-rules.md`](../local-model-rules.md) — LM Studio Infrastructure Gotchas section,
  for the short version of the Auto-Evict/Engine-Protocol findings above.
