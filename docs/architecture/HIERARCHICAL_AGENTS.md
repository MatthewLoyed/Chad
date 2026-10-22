# Hierarchical agents

Status: current implementation guide. Authority:
[agent factory](../../src/core/orchestration/agents/factory.py),
[supervisor](../../src/core/orchestration/supervisor.py) and
[handoffs](../../src/core/orchestration/handoffs.py) in this checkout.

## Supervisor and specialists

The supervisor owns the user-facing turn and delegates tool domains to compiled
specialist graphs. The factory builds each specialist with its domain's tool
subset and persona scope. Registration does not imply activation: check
`disabled_subagents` and per-profile preferences in the effective configuration.

<!-- agent-roster:start -->
| Registered specialist | Tool domain |
|---|---|
| `ui_automation_agent` | `ui_automation` |
| `browser_agent` | `browser` |
| `research_agent` | `web_research` |
| `code_agent` | `code` |
| `system_agent` | `system` |
| `traversal_agent` | `traversal` |
| `canvas_agent` | `canvas` |
<!-- agent-roster:end -->

This block is derived from `AGENT_CONFIGS`. Run
`python scripts/dev/check_docs.py --write-roster` after changing the registry;
keep domain explanations here authored. Communication/Workspace/Technical/Memory
were older roster names, not additional agents to recreate.

## Context and handoffs

[Handoffs](../../src/core/orchestration/handoffs.py) propagate task context and
execution configuration, invoke specialists on namespaced threads, and return
curated results rather than copying every raw tool message into the supervisor.
Parallel research workers use separate thread IDs to avoid checkpoint collisions.

Specialists inherit configured execution limits; there is no universal
three-tool-call cap. Consult the [limit registry](../../src/core/tools/limits.py)
and configuration before changing budgets. Model overrides are resolved from
preferences and factory defaults. See [model lifecycle](../features/MODEL_LIFECYCLE.md)
for residency and swapping costs on local hardware.

## Persistence and verification

[LanggraphEngine](../../src/core/orchestration/langgraph_engine.py) compiles the
supervisor graph and selects a checkpointer: durable local SQLite when Supabase
is absent, Postgres when configured, or ephemeral memory after connection failure.
The [checkpoint module](../../src/core/orchestration/graph/checkpointing.py) guards
serialization. Thread state is distinct from long-term personal memory.

When adding a specialist, update the factory, domain/tool routing, persona and
relevant skill association. Follow the [skill taxonomy](skill-system.md) rather
than creating a parallel registration path. Use [eval guidance](../../evals/AGENTS.md)
for verification and [session isolation](SESSION_ISOLATION.md) for thread boundaries.

## Decisions and tradeoffs

Scoped tools and expertise reduce supervisor context load. Summaries should
include evidence and artifact references needed downstream, not a duplicate
history. Avoid fixed latency claims: measure on the actual model and backend.
