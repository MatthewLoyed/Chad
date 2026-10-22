# System Bootstrapping Architecture

This document outlines the standardized procedures for constructing the CHAD assistant ecosystem, preventing "architectural drift" between GUI (Desktop) and Headless (Cloud) execution flows.

## 🏭 SystemFactory

The `SystemFactory` (`src/application/system_factory.py`) is the single source of truth for component instantiation. It guarantees that every critical dependency graph—such as injecting the `MemoryEngine` into the `ModelRouter`—is preserved identically across all entry points.

### Phase 1: Core Engine (Synchronous)

Constructed via `SystemFactory.build_core_engine()`.
This stage builds foundational blocking logic quickly:

1. **EventBus**: Decoupled pub/sub system.
2. **ToolRegistry**: Discovers all local python tools.
3. **MemoryEngine**: Establishes persistent DB connection.
4. **ModelRouter**: Sets up dynamic routing (Injects `MemoryEngine`).
5. **LiteLLMAdapter**: Shared AI client (Injected into registry for sub-agents).

### Phase 2: Async Infrastructure (Asynchronous)

Activated via `SystemFactory.warmup_async_infrastructure()`.
Runs high-latency or blocking I/O operations in parallel:

- **MCP Registry Prewarm**: Boots/pings registered model context protocol servers.
- **Skill Catalog Scan**: Builds dynamic context primitives.
- **BackgroundAgentManager**: Powers autonomous detach capabilities.

## 📦 SystemContainer

All constructed modules are bundled into a `SystemContainer` dataclass (`src/application/system_container.py`). This strictly structured box is passed across runtime environments, making it explicitly obvious which components are initialized and preventing arbitrary `getattr`/`setattr` antipatterns.

## 🛤️ Execution Paths

Both core pipelines now converge exactly on this architecture:

### GUI Path
`main_gui.py` -> `AppController` -> `ChadAssistant` -> **`SystemFactory`**

### Remote Path
`remote/server.py` -> `RemoteFactory` -> **`SystemFactory`** -> `SessionManager`

## 🔍 Verification
Bootstrap integrity is verified on every major infrastructure change via the rigorous inspection script:
`tests/verification/verify_system_factory.py`
