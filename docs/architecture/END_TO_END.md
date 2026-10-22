# Chad: project map and message lifecycle

Status: current implementation guide. Authority: linked source/configuration in
this checkout; released builds may differ. Domain guides own detailed behavior.

## Find the existing implementation

Use the route for your task, then read only the linked domain guide and source.
Code/configuration describe implemented behavior; the guides explain it. Treat
proposals and archived plans as history until checked against this checkout.

<!-- project-routes:start -->
- **Persistence:** [three message paths](DUALITY_GUIDE.md), [GUI persistence](../../src/remote/gui_limb.py), [command persistence](../../src/application/commands/message_commands.py); check [session isolation](SESSION_ISOLATION.md) and [isolation tests](../../tests/unit/test_orchestrator_session_isolation.py).
- **Specialists:** [agent design](HIERARCHICAL_AGENTS.md), [agent registry](../../src/core/orchestration/agents/factory.py), [handoffs](../../src/core/orchestration/handoffs.py); check [eval guidance](../../evals/AGENTS.md).
- **Memory:** [memory and context](MEMORY_AND_CONTEXT.md), [graph initialization](../../src/core/intelligence/graph_rag_client.py); check [graph tests](../../tests/unit/test_personal_knowledge_graph_schema.py).
- **Coding:** [existing coding harness](CHAD_CODE.md), [dispatch implementation](../../src/tools/dev/openhands_dispatch.py); use its documented verification before proposing another harness.
- **Compaction:** [context lifecycle](MEMORY_AND_CONTEXT.md), [turn compaction](../../src/core/orchestration/turn/consolidation.py), [summary safeguards](../../src/core/utils/consolidation_manager.py); check [bounded checkpoint tests](../../tests/unit/test_turn_consolidator_bounded_checkpoint.py).
- **UI:** [React client](../../src/clients/web/), [desktop entry](../../main_gui.py), [UI verification](../guides/UI_TESTING.md).
- **Tools and skills:** [tool registration](../../src/infrastructure/tools/README.md), [skill taxonomy](skill-system.md), [selection](../features/LLM_TOOL_SELECTOR.md); check [tool contract guidance](../../evals/AGENTS.md).
- **Failures and limits:** [failure policy](FAILURE_AND_LIMITS.md), [shared failure formatter](../../src/core/tools/failure.py), [limit registry](../../src/core/tools/limits.py).
<!-- project-routes:end -->

For startup see [bootstrap](SYSTEM_BOOTSTRAP.md); for live debugging see
[tracing](../guides/DEBUGGING_AND_TRACING.md). Optional CodeGraph queries can help
navigate symbols. If no index exists, use file search and these routes; the
index is not required to contribute or run CI.

## One message through Chad

1. The desktop [entrypoint](../../main_gui.py) starts a Limb with the
   [React client](../../src/clients/web/). The Limb sends user input to the
   [Brain server](../../src/remote/server.py) over WebSocket.
2. [Turn coordination](../../src/core/orchestration/turn_coordinator.py) assembles
   history, persona and relevant resources before invoking the graph. The
   [three transport paths](DUALITY_GUIDE.md) share execution, but differ in
   persistence. Persist the current message after dispatch to avoid duplication.
3. [Tool selection](../features/LLM_TOOL_SELECTOR.md) shortlists capabilities;
   [skills](skill-system.md) expose expertise progressively. Detailed selection
   behavior belongs in those guides, not copied threshold tables here.
4. The [supervisor](../../src/core/orchestration/supervisor.py) can act directly
   or delegate to [domain specialists](HIERARCHICAL_AGENTS.md). Each specialist
   receives its scoped tools and prompt; handoffs return compact results.
5. [Model routing](../../src/core/intelligence/model_router.py) and the
   [local model configuration](../features/LOCAL_MODELS.md) determine the backend.
   Check effective config/preferences rather than assuming a model name.
6. The graph retains execution state through its
   [checkpoint implementations](../../src/core/orchestration/graph/checkpointing.py).
   Local-only installs use SQLite; configured Supabase installs use Postgres.
   In-memory fallback is ephemeral. Personal facts are a separate
   [memory subsystem](MEMORY_AND_CONTEXT.md), not the conversation checkpoint.
7. Results and tool events stream back to the UI. Verify UI behavior using the
   [UI testing guide](../guides/UI_TESTING.md), not a backend-only test.

## Changing behavior safely

Read the task's owning guide and current source before changing it. Preserve
[session isolation](SESSION_ISOLATION.md) and the production port boundary in
[shared guidance](../../AGENTS.md). Run the relevant tests from the route, then
follow [contribution checks](../../CONTRIBUTING.md).

## Decisions and tradeoffs

- This page is a navigation map, not another copy of each subsystem manual.
  Detailed constants, inventories and procedures live with their owning domain.
- Proposed work belongs in issues; historical plans and research explain prior
  reasoning but do not define implemented behavior. See the
  [documentation lifecycle](../../CONTRIBUTING.md#documentation-lifecycle).
- A code index helps navigate symbols but cannot establish live correctness.
  Source, configuration and appropriate tests resolve disagreements with a map.
