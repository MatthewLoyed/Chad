# Message Delivery Architecture: Three Paths to the Brain

Chad has **three** distinct paths by which a user message can reach the Brain and
`turn_coordinator.dispatch()`.  They share all AI logic (TurnCoordinator, LangGraph,
tool registry, adapters) but differ in transport and persistence.

The desktop window marks its URL with `chadDesktop=1` before loading the page.
The web client leaves that page (and `file:` pages) on the native bridge path,
even if pywebview injection is slow. Browser mode is never selected by a desktop
startup timeout. Both frozen entry points divert multiprocessing workers before
application imports; starting a Brain does not kill an existing port owner.

---

## The Three Paths

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PATH 1 — GUI WebSocket (primary, everyday use)                                 │
│  ─────────────────────────────────────────────                                  │
│  src/clients/desktop/api/chat.py                                                │
│    └─► uplink_limb.send_input()   [src/remote/gui_limb.py]                     │
│          └─► WebSocket ──► Brain server                                         │
│                              └─► limb_coordinator.py                            │
│                                    └─► turn_coordinator.dispatch()              │
│                                                                                 │
│  Persistence: gui_limb.py — user msg + assistant saved in turn_complete handler │
│               AFTER the Brain responds (never before dispatch).                 │
│  Used by: The desktop GUI (main_gui.py) always takes this path.                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│  PATH 2 — REST Command Relay (programmatic / headless clients)                  │
│  ─────────────────────────────────────────────────────────────                  │
│  HTTP POST /api/command  {"command_class": "SendMessageCommand", ...}           │
│    └─► src/remote/endpoints.py                                                  │
│          └─► CommandHandler.execute()                                           │
│                └─► SendMessageCommand.execute()  [message_commands.py]          │
│                      └─► AssistantFacade.send_message()                         │
│                            └─► TextModeClient.send_message()                    │
│                                  └─► turn_coordinator.dispatch()                │
│                                                                                 │
│  Persistence: message_commands.py — user msg + assistant saved AFTER turn.     │
│  Used by: Telegram bot, programmatic API clients, test_chat_compaction.py.      │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│  PATH 3 — TextModeClient direct (ephemeral / sub-brain)                        │
│  ──────────────────────────────────────────────────────                         │
│  TextModeClient.send_message()  [src/core/interfaces/text_mode.py]             │
│    └─► turn_coordinator.dispatch()                                              │
│                                                                                 │
│  Persistence: NONE — LangGraph checkpointer is the only state store.           │
│  Used by: integration tests (test_prompt_dry_run, test_pipeline_e2e),           │
│           Skill sub-brains (base_skill.py).                                     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## What Is and Is NOT Shared

| Layer | Shared? | Note |
|---|---|---|
| `turn_coordinator.dispatch()` | ✅ All three paths | Single source of truth for LangGraph execution |
| `LanggraphEngine` / LangGraph graph | ✅ All three paths | |
| `ToolRegistry`, tool execution | ✅ All three paths | |
| AI adapters (LiteLLM, OpenAI, etc.) | ✅ All three paths | |
| WebSocket transport | ❌ Path 1 only | |
| History-store persistence | ❌ Paths 1 & 2 only | Path 3 is intentionally ephemeral |
| GUI push events | ❌ Path 1 only | `push_ai_response`, `push_thinking_event`, etc. |

---

## ⚠️ Cardinal Rule — applies to ALL paths

> **Never write the user message to the history store BEFORE calling `turn_coordinator.dispatch()`.**

The configured history store is local SQLite in local-only installs or Supabase
when configured. `turn_coordinator.dispatch()` loads DB history at the START of every dispatch.
If the current user message is already in the DB when dispatch runs, it will appear
in loaded history AND be injected again as the live message — producing a duplicate
in the LangGraph message channel.

**Always defer persistence until AFTER the turn completes.**

- Path 1: persist in `gui_limb.py`'s `turn_complete` event handler
- Path 2: persist in `SendMessageCommand.execute()` after `facade.send_message()` returns
- Path 3: don't persist at all (ephemeral)

---

## Modifying AI Behavior — Checklist

When changing what the model sees or how it reasons:

| What changed | Files to update |
|---|---|
| Tool schemas | `src/infrastructure/tools/` — shared, one change covers all paths |
| System prompt / persona | `src/core/intelligence/prompt_builder.py` — shared |
| History assembly / deduplication | `turn_coordinator.py`, `langgraph_engine.py` — shared |
| Model adapter (streaming, thinking) | `src/core/intelligence/adapters/` — shared |
| Persistence timing | Update Path 1 (`gui_limb.py`) AND Path 2 (`message_commands.py`) separately |
| GUI-only events (streaming tokens, plan cards) | `gui_limb.py` only |
