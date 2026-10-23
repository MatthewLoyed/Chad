import logging
import os
from typing import Dict, Any, Optional
from src.shared.logger import setup_logger
from src.core.orchestration.agents.specialist_graph import build_specialist_graph
from src.core.intelligence.prompt_builder import SystemPromptBuilder

logger = setup_logger("AgentFactory")

AGENT_CONFIGS = {
    "ui_automation_agent": {
        "domain": "ui_automation",
        "scope": "ui_automation_agent",
        "logger_name": "UIAutomationAgent",
    },
    "browser_agent": {
        "domain": "browser",
        "scope": "browser_agent",
        "logger_name": "BrowserAgent",
        # Inherits the supervisor's active model (e.g. prism-ml/bonsai-27b) by default
        # to prevent expensive multi-gigabyte VRAM thrashing between supervisor and browser_agent.
        # Browser execution always shares the supervisor; stale per-machine pins are ignored.
        # SOURCE OF TRUTH: the browser_agent prompt is the persona fragment
        # `browser_agent` — prompts/persona/browser_agent.md, overridable per user by
        # config/persona/browser_agent.md (#589). This fallback is a minimal safety net
        # used ONLY if that fragment is missing — keep it short so the two do not diverge
        # and cause confusion. Edit the real prompt in the persona file, not here.
        "fallback_prompt": (
            "You are the browser specialist. Drive the user's real logged-in Chrome via the "
            "Playwright tools: browser_navigate to the URL, browser_snapshot to read the page "
            "(a pruned a11y tree + numbered Set-of-Mark menu with ref=eXXX handles), then act "
            "with the EXACT ref (never selectors, never raw JS). Re-snapshot after the page "
            "changes. READ-ONLY: if you hit a login/sign-in page, STOP and report 'LOGIN "
            "REQUIRED' — never type credentials or loop. Report the concrete values you read."
        ),
    },
    "research_agent": {
        "domain": "web_research",
        "scope": "research_agent",
        "logger_name": "ResearchAgent",
        "model_override": {
            "model_name": "qwen/qwen3.5-9b",
            "provider": "lmstudio",
            "thinking_level": "high",
        },
        "fallback_prompt": (
            "You are a web research specialist for deep, multi-item research. "
            "Use the web_search tool: pass all your queries in one call (parallel batch) — "
            "never research one item per turn. Issue focused sub-queries up to the batch cap "
            "(see tools.web_search_max_queries in config). Report facts and links from returned "
            "content only; never invent URLs or details. "
            "SYNTHESIS: analyze results and summarize. Contradictory sources are fine — report "
            "consensus and note dissent in one line; do not over-reconcile. "
            "For YouTube: `site:youtube.com <topic>` with `fetch_content=false`, return "
            "video_id/URL + title to the supervisor for show_youtube_video."
        ),
    },
    "code_agent": {
        "domain": "code",
        "scope": "code_agent",
        "logger_name": "CodeAgent",
        "model_override": {
            # 14B fits 100% in 16GB VRAM with zero CPU offload on Radeon RX 9070 XT
            "model_name": "qwen/qwen2.5-coder-14b",
            "provider": "lmstudio",
            "thinking_level": "medium",
        },
        "fallback_prompt": (
            "You are a coding specialist. Write clean, runnable code.\n"
            "1. DELIVERABLES: HTML/CSS/JS artifacts use build_html_file directly (no sandbox). Never write download links in text.\n"
            "2. VERIFICATION: write_file and build_html_file reject syntax errors — read gate error, fix, and retry. For Python, run_sandboxed_python and confirm zero traceback.\n"
            "3. MOTION: For video/animations, write self-contained HTML/SVG keyframes and call render_animation(html=..., mode='video', duration_ms=...). Do not install Playwright.\n"
            "4. Follow any active skill SOP in your system message exactly."
        ),
    },
    "system_agent": {
        "domain": "system",
        "scope": "system_agent",
        "logger_name": "SystemAgent",
    },
    "traversal_agent": {
        "domain": "traversal",
        "scope": "traversal_agent",
        "logger_name": "TraversalAgent",
        "model_override": {
            "model_name": "qwen/qwen3.5-9b",
            "provider": "lmstudio",
            "thinking_level": "high",
        },
        # Fallback if traversal_agent prompt is not yet in Supabase
        "fallback_prompt": (
            "You are a file traversal specialist. "
            "Your job is to locate and retrieve files across Google Drive and the local filesystem. "
            "Use the fewest tool calls possible. Always use IDs returned by previous calls — never guess. "
            "Stop as soon as you have retrieved the requested file or confirmed it does not exist."
        ),
    },
    "canvas_agent": {
        "domain": "canvas",
        "scope": "canvas_agent",
        "logger_name": "CanvasAgent",
        "fallback_prompt": (
            "You are a Canvas LMS coursework specialist. "
            "Your job is to navigate courses, search modules, find and download lecture files, and inspect assignments or syllabus. "
            "Always pass human-readable course names with fuzzy matching. "
            "Return clean, structured summaries and downloaded file paths to the Supervisor."
        ),
    },
}

def get_agent_model_override(agent_name: str) -> Optional[Dict[str, Any]]:
    """Single model-pin policy for graph construction, residency and fan-out."""
    if agent_name == "browser_agent":
        return None
    from src.shared.config_manager import ConfigManager
    overrides = ConfigManager.get("subagent_model_overrides") or {}
    if ConfigManager.get("setup.share_main_model", False):
        return overrides.get(agent_name) or None
    return overrides.get(agent_name) or AGENT_CONFIGS.get(agent_name, {}).get("model_override")


def _build_model_override(override_cfg: Dict[str, Any]) -> Any:
    """Construct a ChatChad backed by a new LiteLLMAdapter for a per-agent model pin."""
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    from src.core.intelligence.lc_wrapper import ChatChad
    from src.shared.config_manager import ConfigManager

    provider = override_cfg.get("provider", "lmstudio")
    provider_cfg = ConfigManager.get_local_provider_config(provider) or {}

    # Resolve the model's ModelProfile so the adapter applies the correct thinking-param
    # style (e.g. qwen3 reasoning_effort). Without a profile the adapter falls back to the
    # legacy path, which leaves thinking OFF for any model not in config.yaml — that's why
    # the browser_agent's Qwen3-VL-8B reasoning never reached the UI. See config/model_registry.py.
    from config.model_registry import get_profile
    profile = get_profile(override_cfg["model_name"])

    adapter = LiteLLMAdapter(
        model_name=override_cfg["model_name"],
        base_url=provider_cfg.get("base_url", "http://localhost:1234/v1"),
        api_key=provider_cfg.get("api_key", "lm-studio"),
        profile=profile,
    )
    thinking_level = override_cfg.get("thinking_level", "medium")
    temperature = override_cfg.get("temperature", 0.7)
    return ChatChad(adapter=adapter, thinking_level=thinking_level, temperature=temperature)


def create_specialist_agent(
    agent_name: str,
    model: Any,
    tool_registry: Any,
    checkpointer: Optional[Any] = None
) -> Any:
    """
    Creates a compiled LangGraph ReAct agent specialized in a specific domain.
    Hides all boilerplate of tool extraction, system prompt building, and graph compilation.
    """
    config = AGENT_CONFIGS.get(agent_name)
    if not config:
        raise ValueError(f"Unknown agent name: {agent_name}. Available: {list(AGENT_CONFIGS.keys())}")

    domain = config["domain"]
    scope = config["scope"]
    logger_name = config["logger_name"]

    logger = setup_logger(logger_name)

    # Per-agent model override (e.g. traversal_agent pins to qwen/qwen3.5-9b with thinking).
    # A user preference (set via the Subagents settings tab) takes precedence over the
    # AGENT_CONFIGS default pin.
    from src.shared.config_manager import ConfigManager
    model_override_cfg = get_agent_model_override(agent_name)

    # Under unit tests, model_override would silently construct a REAL LiteLLMAdapter
    # hitting LM Studio, discarding whatever mock adapter the test injected as `model`
    # (issue #369) — the specialist's own ReAct loop never sees the test's mock
    # responses, so mock markers (e.g. TASK_COMPLETED/CRITICAL_FAILURE) never appear
    # and the supervisor re-delegates the same step indefinitely.
    if model_override_cfg and os.getenv("CHAD_TESTING") == "true":
        logger.info(
            f"[ INIT ] {agent_name} model override skipped under CHAD_TESTING "
            "(using injected test model instead of a real LiteLLMAdapter)."
        )
        model_override_cfg = None

    effective_model = _build_model_override(model_override_cfg) if model_override_cfg else model
    if model_override_cfg:
        logger.info(f"[ INIT ] {agent_name} using pinned model: {model_override_cfg['model_name']}")

    tools = tool_registry.get_langchain_tools_by_domain(domain)
    tool_names = {getattr(t, "name", "") for t in tools}
    logger.info(f"[ INIT ] Creating {agent_name} with {len(tools)} tools (Persistence={'Enabled' if checkpointer else 'Disabled'}).")

    # GUARDRAIL: the browser_agent is useless without the Playwright MCP tools
    # (browser_navigate / browser_snapshot / ...). If they're absent, the Playwright
    # MCP server failed to connect during discovery (npx cold-start, or the Chrome
    # automation profile was locked). Surface it loudly and tell the agent the truth so
    # it reports "browser automation unavailable" instead of hallucinating a result.
    required_browser_tools = {"browser_navigate", "browser_snapshot"}
    browser_tools_missing = domain == "browser" and not required_browser_tools.issubset(tool_names)
    if browser_tools_missing:
        logger.error(
            f"[ INIT ] browser_agent has NO Playwright MCP tools "
            f"(got: {sorted(tool_names)}). The 'playwright' MCP server likely failed to "
            f"connect during discovery. browser_agent cannot drive the DOM this session."
        )

    # Build system prompt; fall back to inline config prompt if Supabase entry is missing
    try:
        prompt = SystemPromptBuilder.build(base_instruction="", scope=scope)
        if domain == 'browser':
            import hashlib
            from src.infrastructure.repositories.persona_store import overlay_ids
            logger.info('[ BROWSER PROMPT ] sha256=%s customized=%s controller=%s',
                hashlib.sha256(prompt.encode('utf-8')).hexdigest(), 'browser_agent' in overlay_ids(),
                bool(ConfigManager.get('orchestration.browser_controller_enabled', False)))
    except Exception as e:
        fallback = config.get("fallback_prompt", "")
        logger.warning(f"[ INIT ] {agent_name} prompt registry miss ({e}); using fallback prompt.")
        prompt = fallback

    if browser_tools_missing:
        prompt = (
            (prompt or "")
            + "\n\n### ⚠️ BROWSER AUTOMATION UNAVAILABLE\n"
            "The Playwright browser tools (browser_navigate, browser_snapshot, ...) are NOT "
            "connected right now, so you cannot open pages or read the DOM. Do NOT pretend to "
            "navigate or invent page contents. Immediately report to the supervisor: "
            "'BROWSER AUTOMATION UNAVAILABLE — the Playwright MCP server is not connected; "
            "the user should restart Chad or check that Chrome is closed for the automation profile.'"
        )

    # Custom graph (not langgraph.prebuilt.create_react_agent, issue #279 follow-up:
    # the stock prebuilt agent has no concept of `_pending_approval` and just loops
    # a gated write action's pending-approval JSON back into the model instead of
    # halting — see docs/features/BROWSER_AUTOMATION.md and specialist_graph.py).
    return build_specialist_graph(
        model=effective_model,
        tools=tools,
        prompt=prompt,
        checkpointer=checkpointer,
        name=agent_name,
    )

def create_specialist_agents(
    model: Any, 
    tool_registry: Any, 
    checkpointer: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Creates the complete team of specialized agents compiled and ready for orchestration.
    """
    from src.shared.config_manager import ConfigManager
    disabled_agents = set(ConfigManager.get("disabled_subagents") or [])

    # If essential Playwright tools are missing from the registry, do not expose browser_agent
    # to the supervisor at all (prevents delegating to a specialist that has no navigation capability).
    if hasattr(tool_registry, "get_langchain_tools_by_domain"):
        browser_tools = {getattr(t, "name", "") for t in tool_registry.get_langchain_tools_by_domain("browser")}
        if not {"browser_navigate", "browser_snapshot"}.issubset(browser_tools):
            logger.warning(
                "[ INIT ] Playwright MCP tools (browser_navigate, browser_snapshot) are offline. "
                "browser_agent is not accessible for this session."
            )
            disabled_agents.add("browser_agent")
    
    return {
        agent_name: create_specialist_agent(agent_name, model, tool_registry, checkpointer)
        for agent_name in AGENT_CONFIGS
        if agent_name not in disabled_agents
    }


def resolve_agent_model_id(agent_name: str) -> Optional[str]:
    """Resolve the model id a specialist agent will actually run on, mirroring the override
    resolution in `create_specialist_agent` (without building the model). Used by
    parallel-delegate fan-out (#513) to check VRAM eligibility before committing to a
    concurrent batch of handoffs.

    Returns None if the agent has no per-agent pin — it rides whichever base model the
    caller passed to `create_specialist_agent` (i.e. the main supervisor's active model).
    """
    config = AGENT_CONFIGS.get(agent_name)
    if not config:
        return None
    from src.shared.config_manager import ConfigManager
    model_override_cfg = get_agent_model_override(agent_name)
    return model_override_cfg.get("model_name") if model_override_cfg else None

