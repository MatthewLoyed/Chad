"""
dry_run.py — FREE payload capture (no LLM call).

Reuses the SpyAdapter technique from tests/integration/test_prompt_dry_run.py, but
exposes it as a reusable harness so the eval runner can push a whole dataset through
the real TurnCoordinator pipeline in milliseconds, with zero model tokens.

`DryRunHarness.capture(query)` returns exactly what the model *would* have received:
the assembled system prompt, message history, and bound tool schemas (post semantic
selection). Assertions in evals/core.py grade that payload.

The harness initializes the registry/prompts/memory ONCE and reuses them across every
case. Offline (no LM Studio) that is milliseconds per case; with the live 9B tool
selector it is one inference per case — keep the selector resident
(``llm_selector_unload_after_select=False`` below) and use ``--concurrency`` so a
50-case suite finishes in roughly wall≈(N/concurrency)×tool_9b_ms, not a reload per case.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Captured payload ─────────────────────────────────────────────────────────


@dataclass
class Payload:
    """What the harness assembled for one prompt (the model's actual input)."""
    query: str
    category: str
    system_prompt: str
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]                 # raw bound schemas
    tool_names: List[str] = field(default_factory=list)
    system_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    error: Optional[str] = None
    # True when Supabase warmup failed and PromptRegistry fell back to hardcoded offline mock
    # prompts (see _warmup_mock_prompts below). system_includes/system_excludes/prompt_contract
    # results are NOT representative of production when this is set.
    prompts_offline: bool = False

    @property
    def tool_count(self) -> int:
        return len(self.tool_names)


def _tool_name(schema: Dict[str, Any]) -> Optional[str]:
    return schema.get("name") or schema.get("function", {}).get("name")


# ── SpyAdapter — intercepts generate() and records the payload ───────────────


def _make_spy_adapter():
    from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse

    class SpyAdapter(AIAdapter):
        """Captures the assembled payload, then short-circuits the turn cleanly."""

        def __init__(self):
            self.model_name = "eval-spy-adapter"
            self.captured_messages: List[Dict[str, Any]] = []
            self.captured_tools: List[Dict[str, Any]] = []

        async def is_ready(self) -> bool:
            return True

        async def generate(self, messages, tools=None, temperature=0.7,
                            on_thinking=None, on_token=None, agent_name=None, **kwargs):
            self.captured_messages = messages
            self.captured_tools = tools or []
            return GenerateResponse(
                text="[eval dry-run: interception complete, no model called]",
                tool_calls=None,
                finish_reason="stop",
            )

        def convert_tool_schemas(self, schemas):
            return [
                {"type": "function", "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": s.get("parameters", {"type": "object", "properties": {}}),
                }}
                for s in schemas if s.get("name")
            ]

        def build_tool_result_message(self, tool_call_id, name, result):
            return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": str(result)}

        def build_assistant_tool_call_message(self, tool_calls, content=None):
            return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    return SpyAdapter()


# ── Offline prompt fallback (when Supabase is unreachable) ───────────────────


def _warmup_mock_prompts():
    """Populate PromptRegistry offline so dry-runs work with no network."""
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    registry = PromptRegistry.get_instance()
    prompts = {
        "identity": "You are CHAD, an agentic AI assistant for Windows.",
        "soul": "Provide high-quality technical assistance.",
        "user": "Always prioritize the user's specific guidelines.",
        "rich_output": "Provide clear outputs using markdown formatting.",
        "agent": "Specialist agent guidelines and protocols.",
        "tool_calling": "Tool calling specifications: format tools correctly.",
        "mcp_instructions": "Guidelines for utilizing MCP servers.",
        "heartbeat": "Background daemon task instructions.",
        "heartbeat_protocol": "Heartbeat protocol details.",
        "planner": (
            "You are Chad's Planner (post-Scout). Turn gather_brief + user goal into executable "
            "JSON steps. Scout already gathered — plan actions, not re-research, unless brief has gaps.\n"
            "Available specialists:\n{active_agents}"
        ),
        "scout": (
            "# SCOUT — Pre-Planner Gather Agent\n"
            "Gather evidence only before planning. Read/research only — no side effects.\n"
            "Use delegate_to_* for heavy retrieval (gather-only tasks). "
            "Use search_for_tool when the menu is incomplete.\n"
            "Available specialists:\n{active_agents}\n"
            "Do NOT emit plan steps — summarize confirmed facts when done."
        ),
        "validator": "You are Chad's Reflection & Quality Validator.",
        "supervisor_routing": "ORCHESTRATION & ROUTING: Coordinate tasks and invoke tools.",
        "subagent_base": "OUTPUT PROTOCOL: Return only the result.",
        # Required in every user-facing scope (prompt_audit._MEMORY_POLICY_REQUIRED_SCOPES);
        # worker scopes (planner/validator/subagents) intentionally strip it.
        "memory_policy": "Personal facts live in the knowledge graph — read/write via manage_user_memory.",
    }
    # Subagent scopes (e.g. research_agent, code_agent) require a same-named prompt entry
    # (SystemPromptBuilder._read_agent_files enforces it) — stub every enabled agent from
    # AGENT_CONFIGS with WORDING DISTINCT ENOUGH to avoid tripping prompt_audit's
    # near-duplicate-fragment check (a shared word-for-word template scores as duplicates).
    _AGENT_MOCK_PROMPTS = {
        "research_agent": "Gather and synthesize information from the web and documents; "
                          "cite sources; never take side-effecting actions.",
        "code_agent": "Read, write, and run code in the project's sandbox; verify changes "
                     "compile and pass tests before reporting done.",
        "traversal_agent": "Navigate and reorganize the local filesystem; move, rename, and "
                           "archive files on explicit user instruction only.",
        "ui_automation_agent": "Drive desktop UI elements (clicks, keystrokes, window focus) "
                               "to complete a task outside the browser.",
        "browser_agent": "Automate a live browser session — navigate, click, and extract "
                         "page content — for tasks a plain web search can't answer.",
        "system_agent": "Inspect and adjust local OS/hardware state (volume, brightness, "
                        "running processes) on explicit request.",
    }
    try:
        from src.core.orchestration.agents.factory import AGENT_CONFIGS
        from src.shared.config_manager import ConfigManager
        disabled = set(ConfigManager.get("disabled_subagents") or [])
        for agent_id in AGENT_CONFIGS:
            if agent_id not in disabled:
                prompts.setdefault(
                    agent_id,
                    _AGENT_MOCK_PROMPTS.get(agent_id, f"You are Chad's dedicated {agent_id}.")
                )
    except Exception:
        pass
    scopes = {k: "always" for k in prompts}
    scopes.update({"planner": "planner", "validator": "validator", "scout": "scout",
                   "supervisor_routing": "supervisor"})
    # Each subagent's own prompt is scoped to ITSELF, not "always" — matching production,
    # where get_prompts_by_scopes([scope, "always", "subagent_base"]) would otherwise pull
    # every other agent's persona into every scope's assembled prompt.
    for agent_id in _AGENT_MOCK_PROMPTS:
        if agent_id in prompts:
            scopes[agent_id] = agent_id
    registry._prompts = prompts
    registry._scopes = scopes
    registry._loaded = True


# ── The reusable harness ─────────────────────────────────────────────────────


class DryRunHarness:
    """Initialize the harness once; capture() many prompts cheaply."""

    # Class-level default so instances built via __new__ without __init__ (e.g. unit tests
    # that stub out setup()) still have this attribute when capture() reads it.
    prompts_offline: bool = False

    def __init__(self, sync_cloud: bool = False, quiet: bool = True):
        self._sync_cloud = sync_cloud
        self._quiet = quiet
        self._client = None
        self._spy = None
        self._counter = None
        self.prompts_offline = False

    async def setup(self):
        os.environ.setdefault("CHAD_TESTING", "true")
        from src.shared.config_manager import ConfigManager
        from src.application.events import EventBus
        from src.infrastructure.tools.registry import ToolRegistry
        from src.core.tools.tool_executor import LocalToolExecutor
        from src.core.interfaces.text_mode import TextModeClient
        from src.core.intelligence.memory_engine import MemoryEngine
        from src.core.utils.token_counter import TokenCounter
        from src.infrastructure.repositories.prompt_registry import PromptRegistry

        ConfigManager.load_config()
        tools_cfg = ConfigManager.get_config().setdefault("tools", {})
        # Dry-run tests OFFLINE tool BINDING, not the production fail-fast safety net.
        # Production intentionally aborts a turn when the 9B selector is unreachable (no
        # silent RAG fallback); a CI/dev box without LM Studio would abort every non-planning
        # turn otherwise, making this lane unrunnable offline. Force the RAG fallback path.
        tools_cfg["llm_selector_fail_fast"] = False
        # Production unloads the 9B after every select so the main chat model keeps VRAM
        # (#632). Dry-run never loads a main model (SpyAdapter) — that unload/reload is
        # pure dead time (~80% of tools=). Keep the selector resident for the batch.
        tools_cfg["llm_selector_unload_after_select"] = False
        # Don't post-turn re-warm the main chat model (Gemma) — dry-run never uses it, and
        # schedule_warm would fight the resident 9B for VRAM (#632). turn_coordinator also
        # skips warm when platform="eval"; this kills the lifecycle gate as belt-and-suspenders.
        local_models = ConfigManager.get_config().setdefault("local_models", {})
        lifecycle = local_models.setdefault("lifecycle", {})
        lifecycle["preload_on_connect"] = False
        # Skip intent-router gemma-1b warmup; platform=eval also bypasses classify entirely.
        ConfigManager.get_config().setdefault("intent_router", {})["skip_warmup"] = True
        # Tool-embedding preamble generation uses the ACTIVE chat model (Gemma 26B) via
        # LiteLLM — that JIT-loads Gemma in parallel with the 9B selector during shortlist
        # (#632). Dry-run only needs binding assertions; static fallbacks are fine.
        ConfigManager.get_config().setdefault("tool_sync", {})["automatic_preambles"] = False
        event_bus = EventBus()

        registry = ToolRegistry(event_bus=event_bus)
        registry.discover_all(sync_cloud=self._sync_cloud)
        # Pay the one-time load here so the first case isn't the batch's long pole and
        # concurrent workers don't all race a cold JIT load.
        try:
            selector = getattr(registry, "_llm_selector", None)
            if selector is not None:
                selector.warmup()
        except Exception:
            pass

        try:
            await PromptRegistry.get_instance().warmup()
        except Exception as e:
            if not self._quiet:
                print(f"[dry_run] Supabase warmup failed ({e}); using offline prompts.")
            _warmup_mock_prompts()
            # Recorded regardless of --verbose — a stdout-only line is easy to miss; the
            # report banner (core.render_report) is the reliable, unmissable signal.
            self.prompts_offline = True

        memory = MemoryEngine()
        self._spy = _make_spy_adapter()
        executor = LocalToolExecutor(tool_registry=registry, memory_engine=memory)
        self._client = TextModeClient(
            adapter=self._spy,
            tool_registry=registry,
            tool_executor=executor,
            memory_engine=memory,
            event_bus=event_bus,
        )
        self._counter = TokenCounter
        return self

    async def capture(self, query: str, category: str = "agent",
                      conversation_id: Optional[str] = None) -> Payload:
        """Push one prompt through the pipeline and capture the assembled payload."""
        import uuid
        conv = conversation_id or f"eval_{uuid.uuid4().hex[:8]}"
        self._spy.captured_messages = []
        self._spy.captured_tools = []

        try:
            # platform="eval" — exclusive interactive priority + eval_exclusivity gate
            # (blocks desktop/cron while this turn runs). Also drives _is_eval_turn skips.
            from src.shared.context import RequestContext
            ctx = RequestContext(platform="eval", session_id=conv)
            await self._client.send_message(text=query, conversation_id=conv, category=category, context=ctx)
        except Exception as e:
            return Payload(query=query, category=category, system_prompt="",
                           messages=[], tools=[], error=f"{type(e).__name__}: {e}")

        messages = self._spy.captured_messages
        tools = self._spy.captured_tools
        system_prompt = ""
        history = []
        for m in messages:
            if m.get("role") == "system":
                system_prompt = m.get("content", "") or ""
            else:
                history.append(m)

        names = [n for n in (_tool_name(t) for t in tools) if n]
        sys_tok = self._counter.count_text(system_prompt) if system_prompt else 0
        tool_tok = self._counter.count_tools(tools) if tools else 0
        total = self._counter.count_total(
            system_instruction=system_prompt, memory_block="", history=history, tools=tools
        )

        return Payload(
            query=query, category=category, system_prompt=system_prompt,
            messages=history, tools=tools, tool_names=names,
            system_tokens=sys_tok, tool_tokens=tool_tok, total_tokens=total,
            prompts_offline=self.prompts_offline,
        )
