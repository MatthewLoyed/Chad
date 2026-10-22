# Subagent Model Swap

## Problem

Several specialist subagents (`code_agent`, `research_agent`, `traversal_agent`) are
pinned via `model_override` in [`agents/factory.py`](../../src/core/orchestration/agents/factory.py)
to a small local model (`qwen/qwen3.5-9b`) that's different from whatever the main
supervisor model is (e.g. a 26B+ chat model). When the supervisor delegates to one of
these, it blocks synchronously on `agent_graph.ainvoke(...)` ([`handoffs.py`](../../src/core/orchestration/handoffs.py),
`subagent_node`) — the supervisor generates zero tokens for the entire subagent run. If
both models are resident in LM Studio at once, the subagent's inference competes for
VRAM/compute with a model that isn't doing anything, which measurably slows it down
(the same effect observed and fixed for the 9B tool selector — see
[`LLM_TOOL_SELECTOR.md`](LLM_TOOL_SELECTOR.md)).

Coding tasks in particular run long (sandbox iteration, multi-step generation), so the
swap latency is comfortably amortized. A quick one-shot delegation might not clear that
bar — see "Open question" below.

The actual unload/reload calls are routed through the shared coordinator in
[`MODEL_LIFECYCLE.md`](MODEL_LIFECYCLE.md) (owner tag `OWNER_MAIN_MODEL`), so this swap no
longer races the tool selector, intent router, or scout's secondary-model gather over the
same LM Studio instance.

## What's implemented (Phase 1 — done)

[`src/core/intelligence/model_swap.py`](../../src/core/intelligence/model_swap.py) —
async LM Studio unload/load-by-name helpers (same instance-resolution approach as
`LLMToolSelector`, ported to httpx since callers are LangGraph coroutines):
- `resolve_instance_id(base_url, model_id)` — `GET /api/v1/models` (native, not the
  OpenAI-compat `/v1/models`), matches `models[].key`, returns `loaded_instances[].id`.
- `unload_model(base_url, model_id)` / `load_model(base_url, model_id)` — wrap
  `POST /api/v1/models/unload` (`{"instance_id": ...}`) and `/load` (`{"model": ...}`).
  Every failure path is non-fatal (logs at debug, returns `False`) — worst case LM
  Studio's own JIT-loading covers it on the next inference call.

[`handoffs.py`](../../src/core/orchestration/handoffs.py) — `subagent_node` (the actual
mounted graph node per agent; `create_handoff_tools`' `handoff_fn` only supplies the
tool *schema* for the supervisor's LLM call, the routing dispatches to `subagent_node`
for execution) now wraps the `ainvoke` call:
```python
swapped = await _swap_for_subagent(agent_name)   # unload main model iff override differs
try:
    result = await agent_graph.ainvoke(...)
finally:
    await _swap_back(swapped)                    # reload main model
```
`_swap_for_subagent` no-ops (returns `None`, nothing to swap back) when: the feature is
config-disabled, the main model isn't local, the agent has no `model_override`, or the
override is the same weights as the main model (nothing to evict). Gated by
`subagents.model_swap_enabled` (default `true`) in `config.yaml`.

This already benefits `code_agent` (the case asked for), and `research_agent`/
`traversal_agent` for free since they share the same condition.

## What's NOT implemented (Phase 2 — this writeup)

A global on/off switch in config isn't what was asked for — the goal is **per-subagent
control from the UI**: pick which model powers each specialist, and whether to unload
the main model while it runs.

### Where it plugs in

The settings UI already has the exact shape needed, just for enable/disable today:

- **UI**: [`SubagentsTab.tsx`](../../src/clients/web/src/components/modals/settings/SubagentsTab.tsx)
  renders a card per agent with an enabled/disabled toggle, backed by
  `window.pywebview.api.get_subagents()` / `.toggle_subagent(name, enabled)`.
- **Bridge**: [`src/clients/desktop/api/config.py`](../../src/clients/desktop/api/config.py)
  (`get_subagents`/`toggle_subagent`) dispatches `GetSubagentsCommand`/`ToggleSubagentCommand`
  through the existing Command pattern.
- **Backend**: [`src/application/facades/assistant/config.py`](../../src/application/facades/assistant/config.py)
  `get_subagents()`/`toggle_subagent()` merge `AGENT_CONFIGS` (static defaults) with a
  user override layer persisted via `ConfigRepository` to `preferences.yaml`
  (`disabled_subagents: [...]`).
- **Model discovery**: [`local_model_discovery.py`](../../src/core/intelligence/local_model_discovery.py)
  already enumerates models LM Studio/Ollama have downloaded — this is what the main
  model picker (`ModelSettingsModal.tsx`) uses today, and is the natural source for a
  per-agent dropdown's options (no new discovery code needed).

### Proposed shape

Extend the same override layer rather than inventing a parallel one:

**`preferences.yaml`** — add a `subagent_overrides` map alongside the existing
`disabled_subagents` list:
```yaml
disabled_subagents: []
subagent_overrides:
  code_agent:
    model_name: "qwen/qwen3.5-9b"   # null/omitted = use main model, no override
    provider: "lmstudio"
    unload_main_while_running: true  # per-agent override of the swap behavior
```

**Backend** (`config.py` facade) — add `get_subagent_model_options()` (wraps
`LocalModelDiscovery`) and extend `get_subagents()`'s per-agent dict with
`model_name`/`unload_main_while_running` (falling back to `AGENT_CONFIGS[name].model_override`
when no preference override exists), plus a new `set_subagent_model(name, model_name,
provider, unload_main_while_running)` command mirroring `ToggleSubagentCommand`.

**`factory.py`** — `create_specialist_agent` currently reads `config.get("model_override")`
straight from the static `AGENT_CONFIGS` dict. It needs to check the persisted
preference override first and fall back to the static config — same merge pattern
`get_subagents()` already does for `disabled_subagents`.

**`handoffs.py`** — `_swap_for_subagent` currently reads the global
`subagents.model_swap_enabled` flag. Change it to read the per-agent
`unload_main_while_running` from the same preference override (defaulting to the global
flag when unset), so a user can turn swapping on for `code_agent` specifically and
leave it off for a subagent whose calls are too short to be worth it.

**UI** (`SubagentsTab.tsx`) — each card gets a model `<select>` (populated from
`get_subagent_model_options()`) and a second toggle (reusing the existing `subagent-toggle`
button style) for "unload main model while running." Needs a live-server caveat: changing
a subagent's model only affects the *next* compiled graph — `create_specialist_agents`
runs once at supervisor build time, so this needs either a supervisor rebuild trigger
(check how `toggle_subagent` currently propagates — likely also needs a restart/rebuild
today) or wiring a config-reload hook.

### Open question worth settling before building

Not every subagent call is long enough to amortize two LM Studio round trips (unload +
reload, each paying model-load latency). Worth instrumenting actual `code_agent` /
`research_agent` / `traversal_agent` call durations (the harness eval transcripts in
`evals/runs/` likely already have this) against typical 9B/main-model load times before
defaulting `unload_main_while_running: true` for agents whose calls run only a few
seconds — for those, the swap could be net negative.
