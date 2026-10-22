# Core Module Architecture

This document defines the responsibility boundaries for each `core/` module to prevent bloat and maintain clear separation of concerns.

## Module Responsibility Matrix

| Module | Responsibility | Max Lines |
|--------|---------------|-----------|
| `session_manager.py` | Orchestration & session lifecycle | 800 |
| `audio_handler.py` | Audio device coordination (facade) | 300 |
| `audio_input.py` | Microphone input stream | 300 |
| `audio_output.py` | Speaker output stream | 300 |
| `background_agents.py` | Detached ReAct task management | 200 |
| `model_router.py` | LLM selection & routing | 300 |
| `memory_engine.py` | Persistent memory & facts | 500 |
| `tool_executor.py` | Tool invocation & formatting | 300 |
| `state_coordinator.py` | UI state transitions | 200 |
| `overlay_manager.py` | Desktop HUD notifications | 300 |
| `mcp_registry.py` | MCP server management | 300 |
| `router_agent.py` | Intent classification | 300 |

## Dependency Flow

The headless Brain boots via `RemoteFactory` (the GUI/Brain split removed the old
`ChadAssistant`/`BootManager` container — see `docs/features/VOICE_MODE.md`).

```
server.py (headless Brain)
    └── RemoteFactory.initialize_remote_system()
            ├── SystemFactory.build_core_engine() → ToolRegistry, ModelRouter,
            │       MemoryEngine, TextModeClient, ...
            └── SessionManager (orchestrator, audio = MockAudio)
                    ├── ModelRouter
                    ├── ToolExecutor (Hybrid: local + remote)
                    └── StateCoordinator

main_gui.py (Limb / client)
    └── GUIChadLimb (WebSocket uplink)  +  VoiceModeHost (mic, wake word, STT, TTS)
```

## Extraction Candidates

If a module exceeds its limit, consider extracting:

| From | Extract To | When |
|------|------------|------|
| `session_manager` | `conversation_lifecycle.py` | Lifecycle logic > 200 lines |
| `session_manager` | `response_dispatcher.py` | Response handling > 200 lines |
| `memory_engine` | `fact_store.py` | Fact persistence isolated |

## Adding New Modules

1. Define clear responsibility in this README
2. Set max line target
3. Use composition pattern (not inheritance)
4. Import at point-of-use to avoid circular deps
