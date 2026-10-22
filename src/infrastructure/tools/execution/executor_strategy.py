"""
Executor Strategy Pattern for Tool Execution

This provides a unified interface for executing different types of tools:
- LocalExecutor: Regular BaseTool instances
- SkillExecutor: BaseSkill instances (sub-agents)
- MCPExecutor: MCP server tools

This enables the ToolRegistry to dispatch tool execution without
knowing the specific tool type.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import asyncio
import logging
import re
import os
import json
from src.shared.logger import setup_logger
from src.core.intelligence.model_capability_registry import get_model_capability_registry
from src.core.tools import failure, limits
from src.core.tools.tool_timeouts import get_tool_timeout

logger = setup_logger("ExecutorStrategy")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
logger.debug(f"PROJECT_ROOT resolved to: {PROJECT_ROOT}")


def _current_turn_key() -> Optional[str]:
    """Turn id for scoping per-turn executor state, or None outside a turn."""
    try:
        from src.core.orchestration.context import turn_id_var, browser_run_id_var

        return browser_run_id_var.get() or turn_id_var.get()
    except Exception:
        return None


class _TurnScopedState:
    """Mixin: drop the named per-turn dicts/flags whenever the turn changes.

    Executors are instantiated ONCE on a process-lifetime ExecutionRoutingEngine,
    so anything they accumulate lives until the Brain restarts. The loop-breaker
    counters were exactly that: a browser_click that failed once and was never
    retried stayed blocked forever, in every later conversation, with no way to
    clear it short of a restart. Scope them to the turn that observed the failure.
    """

    _turn_scoped_attrs: tuple = ()

    def _init_turn_scope(self) -> None:
        """Adopt the current turn at construction.

        Not a sentinel that forces a reset on first use: an executor built
        mid-turn belongs to that turn, and a spurious first-call reset would
        discard state a caller had already established.
        """
        self._turn_key = _current_turn_key()

    def _reset_state_on_new_turn(self) -> None:
        key = _current_turn_key()
        if getattr(self, "_turn_key", None) == key:
            return
        self._turn_key = key
        for attr in self._turn_scoped_attrs:
            current = getattr(self, attr, None)
            if isinstance(current, dict):
                current.clear()
            else:
                setattr(self, attr, False)


def _sanitize_mcp_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Drop null/empty optional args before MCP invocation.

    Local models often emit explicit JSON null for omitted optional fields (e.g.
    pageSize: null). MCP servers backed by Zod reject null for number types —
    they expect the key to be absent instead.
    """
    cleaned: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = value
    return cleaned


_DRIVE_Q_MARKERS = (
    " contains", " mimeType", " name", " fullText",
    " modifiedTime", " createdTime", " viewedByMeTime",
    " parentId", " parents", " trashed", " starred",
    " owner", " visibility", " sharedWithMe",
    " and ", " or ", " not ",
)
_DRIVE_Q_OPERATORS = ("=", "!=", "<=", ">=", "<", ">", " in ")


def _looks_like_drive_api_query(query: str) -> bool:
    q = query.strip()
    if not q:
        return False
    q_lower = f" {q.lower()} "
    return any(m in q_lower for m in _DRIVE_Q_MARKERS) or any(op in q for op in _DRIVE_Q_OPERATORS)


def _coerce_mcp_search_params(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Google Drive search: rawQuery=true requires Drive API q syntax.

    Models often set rawQuery=true with plain keywords (e.g. query="wedding"),
    which the Drive API rejects as Invalid Value. Drop rawQuery unless the query
    already looks like structured Drive q syntax.
    """
    if tool_name != "search" or not params.get("rawQuery"):
        return params

    query = params.get("query")
    if not isinstance(query, str) or _looks_like_drive_api_query(query):
        return params

    coerced = dict(params)
    coerced.pop("rawQuery", None)
    logger.debug(
        f"[ MCP ] Dropped rawQuery for plain-text Drive search {query!r} "
        "(MCP will wrap as fullText contains)"
    )
    return coerced


def _normalize_playwright_target_params(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Defensive coercion for browser_* target refs (issue #287).

    The Set-of-Mark menu (snapshot_pruner.render_set_of_mark) tells the model to pass
    the bare ref (e.g. target="e34"), but smaller local models still sometimes emit a
    near-miss: the old "ref=e34" form, a `selector` key instead of `target`, or the
    ref digits without the leading "e". Coerce those rather than hard-failing the
    Playwright call ("Unknown engine" / missing-target errors) and forcing a retry.
    """
    if not tool_name.startswith("browser_") or not params:
        return params

    coerced = dict(params)
    if "target" not in coerced and "selector" in coerced:
        coerced["target"] = coerced.pop("selector")

    target = coerced.get("target")
    if isinstance(target, str):
        stripped = target.strip()
        if stripped.lower().startswith("ref="):
            stripped = stripped.split("=", 1)[1].strip().strip('"\'')
        if stripped.isdigit():
            stripped = f"e{stripped}"
        if stripped != target:
            logger.debug(f"[ MCP ] Normalized {tool_name} target {target!r} -> {stripped!r}")
            coerced["target"] = stripped

    return coerced


def _extract_image_b64(mcp_result: list) -> Optional[tuple]:
    """Pull (base64_data, mime_type) out of an MCP image content item, or None."""
    for item in mcp_result:
        if isinstance(item, dict):
            item_type, data, mime = item.get("type"), item.get("data"), item.get("mimeType")
        else:
            item_type, data, mime = getattr(item, "type", None), getattr(item, "data", None), getattr(item, "mimeType", None)
        if item_type == "image" and data:
            return data, mime or "image/png"
    return None


def _normalize_lmstudio_model_id(model_id: str, base_url: str) -> str:
    if not any(model_id.startswith(p) for p in ("openai/", "ollama/", "lm_studio/")):
        prefix = "ollama/" if "ollama" in base_url.lower() else "openai/"
        return f"{prefix}{model_id}"
    return model_id


async def _vision_assist_resolve_target(server: Any, failing_call_sig: str) -> Optional[str]:
    """
    Vision-assist fallback (DOM Path A hybrid): once the loop-breaker has blocked a
    repeated failing browser_click/browser_type-style call, take a screenshot + a fresh
    accessibility snapshot and ask Qwen3-VL-8B (or configured model) which numbered element
    actually matches — the browser_agent model (still driving the loop, text-only) gets the
    answer as a hint on its next turn rather than the vision model taking over the loop itself.
    Primary vision path is the Fara-7B screenshot-native loop (issue #288 / evals/browser_fara_probe.py).

    NOTE: this was written when browser_agent was pinned to gpt-oss-20b (~12GB), which
    plus qwen3-vl-8b (~6GB) fit together in VRAM without an unload/reload dance — see
    handoffs.py _swap_for_subagent for the heavier swap path used elsewhere. browser_agent
    is now pinned to google/gemma-4-26b-a4b-qat (~16GB on its own) to match the model
    proven by the hybrid DOM+feedback probe; that assumption may no longer hold on a
    16GB-VRAM box. If vision-assist starts OOMing or double-loading (see the
    lmstudio-heavy-model-double-load lesson), this JIT-load-alongside approach needs an
    explicit unload/reload of the browser model first, not a naive parallel load.

    Soft-fails to None (caller falls back to its existing generic BLOCKED guidance) on
    any missing prereq: no VL profile registered, screenshot/snapshot tool unavailable,
    or the vision model itself unreachable. Never raises.
    """
    from src.shared.config_manager import ConfigManager
    from config.model_registry import get_profile
    from src.core.intelligence.model_lifecycle import ensure_resident, current_priority
    import litellm
    from src.infrastructure.tools.execution.snapshot_pruner import prune_snapshot, render_set_of_mark

    # Check if vision assist is enabled
    if not ConfigManager.get("tools.browser_vision_assist_enabled", False):
        return None

    vision_model = ConfigManager.get("tools.browser_vision_assist_model", "qwen/qwen3-vl-8b")
    profile = get_profile(vision_model)
    if not profile:
        logger.debug(f"[ VISION_ASSIST ] No profile found for '{vision_model}'; skipping vision assist.")
        return None

    provider_cfg = ConfigManager.get_local_provider_config("lmstudio") or {}
    base_url = profile.base_url or provider_cfg.get("base_url", "http://localhost:1234/v1")
    api_key = provider_cfg.get("api_key", "lm-studio")

    try:
        shot_result = await asyncio.wait_for(server.call_tool("browser_take_screenshot", {}), timeout=30)
        snap_result = await asyncio.wait_for(server.call_tool("browser_snapshot", {}), timeout=30)
    except Exception as e:
        logger.debug(f"[ VISION_ASSIST ] Could not capture screenshot/snapshot: {e}")
        return None

    image = _extract_image_b64(shot_result)
    if not image:
        logger.debug("[ VISION_ASSIST ] browser_take_screenshot returned no image content.")
        return None
    image_data, mime = image

    som_menu = ""
    for item in snap_result:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            pruned, _, _ = prune_snapshot(text)
            som_menu = render_set_of_mark(pruned)
            break

    # Claim the VL model resident alongside the browser model via the shared coordinator —
    # ensure_resident is idempotent (a no-op if already loaded) and, when a VRAM budget is
    # configured (local_models.lifecycle.vram_budget_gb), makes an actual size-aware decision
    # about whether the two models fit together instead of assuming they always will.
    if not await ensure_resident(base_url, vision_model, owner="vision_assist", priority=current_priority()):
        return None

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise UI grounding assistant. Given a screenshot and a "
                    "numbered menu of interactive elements, identify which numbered element "
                    "matches the described target. Reply with ONLY the exact target=\"eXX\" "
                    "value from the menu — no explanation."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"This automation call keeps failing: {failing_call_sig}\n\n"
                            f"Numbered elements on screen:\n{som_menu or '(no menu available)'}\n\n"
                            "Which target=\"eXX\" should be used instead?"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
                ],
            },
        ]
        req: Dict[str, Any] = {
            "model": _normalize_lmstudio_model_id(vision_model, base_url),
            "messages": messages,
            "api_base": base_url,
            "api_key": api_key,
            "max_tokens": 128,
            "temperature": 0.2,
            "stream": False,
        }
        if profile.thinking_param_style == "qwen3":
            req["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        litellm.drop_params = True
        from src.core.intelligence.model_lifecycle import active_use
        async with active_use(base_url, vision_model):
            response = await litellm.acompletion(**req)
        answer = (response.choices[0].message.content or "").strip()
        return answer or None
    except Exception as e:
        logger.debug(f"[ VISION_ASSIST ] Vision model call failed: {e}")
        return None


def _policy_approval_gate(tool_name: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Risk-based approval gate for executors whose tools have no Python class to
    set requires_approval (MCP, skills). Returns a short-circuit result dict
    (pending pause or user-declined) when the policy gates this tool, else None.
    Unlike the native gate it never injects decision params — MCP/skill calls
    forward their params verbatim to the server/sub-agent.
    """
    from src.core.tools.tool_policy_manager import get_policy_manager

    if not get_policy_manager().requires_approval(tool_name):
        return None

    from src.core.orchestration.approval_gate import evaluate_approval_gate

    outcome = evaluate_approval_gate(tool_name, decision_type="accept_decline", params=params)
    return outcome.short_circuit


class ExecutorStrategy(ABC):
    """
    Abstract base class for tool execution strategies.
    """

    @abstractmethod
    async def execute(self, tool: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool with given parameters.

        Args:
            tool: The tool instance (BaseTool, BaseSkill, or MCPServerEntry)
            params: Execution parameters

        Returns:
            Result dictionary with 'success', 'result', and optionally 'error'
        """
        pass

    @abstractmethod
    def can_handle(self, tool: Any) -> bool:
        """
        Check if this executor can handle the given tool.

        Args:
            tool: The tool instance to check

        Returns:
            True if this executor can handle the tool
        """
        pass


class LocalExecutor(ExecutorStrategy, _TurnScopedState):
    """
    Executor for regular BaseTool instances.
    """

    # Both are evidence about THIS turn: a bad E2B key or a failing command in
    # one conversation says nothing about the next one.
    _turn_scoped_attrs = ("_sandbox_call_failure_counts", "_sandbox_auth_failed")

    def __init__(self) -> None:
        # Sandbox loop-breaker: identical failing E2B calls (issue #287 pattern).
        self._sandbox_call_failure_counts: Dict[str, int] = {}
        self._sandbox_auth_failed = False
        self._init_turn_scope()

    def _sandbox_call_signature(self, tool_name: str, params: Dict[str, Any]) -> str:
        return f"{tool_name}:{json.dumps(params, sort_keys=True, default=str)}"

    def _sandbox_preflight(self, tool_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from src.tools.coding.sandbox import SANDBOX_TOOL_NAMES

        if tool_name not in SANDBOX_TOOL_NAMES:
            return None

        self._reset_state_on_new_turn()

        if self._sandbox_auth_failed:
            return failure.failure(
                "E2B sandbox authentication already failed earlier in this session.",
                failure_class=failure.AUTH,
                tool_name=tool_name,
                next_actions=[
                    "Do NOT retry any sandbox tool — the same key will be rejected again.",
                    "Report the E2B credentials problem to the user and move on.",
                ],
            )

        call_sig = self._sandbox_call_signature(tool_name, params)
        if self._sandbox_call_failure_counts.get(call_sig, 0) >= int(
            limits.get("repeat_failure_block_after")
        ):
            return failure.failure(
                f"'{tool_name}' with these exact arguments already failed once.",
                failure_class=failure.BLOCKED,
                tool_name=tool_name,
                next_actions=[
                    "Repeating the identical sandbox command is blocked and will not run.",
                    "Change the command, or switch approach entirely.",
                    "If the earlier error was 401/Unauthorized, report the credentials "
                    "problem to the user instead of trying again.",
                ],
            )
        return None

    def _sandbox_note_result(self, tool_name: str, params: Dict[str, Any], result: Dict[str, Any]) -> None:
        from src.tools.coding.sandbox import SANDBOX_TOOL_NAMES

        if tool_name not in SANDBOX_TOOL_NAMES or not isinstance(result, dict):
            return

        if result.get("success") is True:
            call_sig = self._sandbox_call_signature(tool_name, params)
            self._sandbox_call_failure_counts.pop(call_sig, None)
            return

        # A credentials failure poisons every later sandbox call this session, so
        # latch it. Both `auth` (401) and `config` (no/blank key) qualify — neither
        # can be fixed by anything the model does.
        if result.get("failure_class") in (failure.AUTH, failure.CONFIG):
            self._sandbox_auth_failed = True

        call_sig = self._sandbox_call_signature(tool_name, params)
        self._sandbox_call_failure_counts[call_sig] = (
            self._sandbox_call_failure_counts.get(call_sig, 0) + 1
        )

    def can_handle(self, tool: Any) -> bool:
        """Check if tool is a BaseTool instance."""
        try:
            from src.tools.base_tool import BaseTool
            # Primary check
            if isinstance(tool, BaseTool):
                return True
            
            # Fallback for identity issues across reload/paths
            # Check if the class name or base class names match
            cls_name = tool.__class__.__name__
            if cls_name == "BaseTool" or any(base.__name__ == "BaseTool" for base in tool.__class__.__mro__):
                return True
                
            return False
        except Exception:
            return False

    async def execute(self, tool: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a BaseTool.

        Handles validation, before/after hooks, and error handling.
        """
        from src.tools.base_tool import BaseTool

        if not isinstance(tool, BaseTool):
            return failure.failure(
                "Tool is not a BaseTool instance", failure_class=failure.CAPABILITY_GAP
            )

        tool_name = getattr(tool, "name", "")

        try:
            blocked = self._sandbox_preflight(tool_name, params)
            if blocked is not None:
                return blocked

            # Validate parameters
            is_valid, error_msg = tool.validate_params(**params)
            if not is_valid:
                logger.error(f"Parameter validation failed for {tool.name}: {error_msg}")
                return failure.failure(
                    f"Invalid parameters: {error_msg}",
                    failure_class=failure.BAD_INPUT,
                    tool_name=tool_name,
                )

            # Human-in-the-loop gate: a tool pauses here when it opts in
            # (requires_approval / wants_approval) OR when the risk-based policy
            # (config/tool_policies.yaml) matches its name. Resumes once a
            # decision is recorded. See src/core/orchestration/approval_gate.py.
            gated = self._check_approval_gate(tool, params)
            if gated is not None:
                return gated

            # Run before_execute hook
            params = await tool.before_execute(**params)

            # Execute the tool, bounded so a stalled tool (e.g. an interactive
            # auth flow waiting on a browser callback) can't hang the event loop
            # forever and starve everything else sharing it (WebSocket pings, etc.)
            timeout = get_tool_timeout(tool.name)
            result = await asyncio.wait_for(tool.execute(**params), timeout=timeout)

            # Run after_execute hook
            result = await tool.after_execute(result)

            # Whole-fleet coverage: a tool that still returns a bare
            # {"success": False, "error": "..."} gets classified here rather than
            # reaching the model as an unactionable string. Tools that classified
            # themselves are left alone.
            result = failure.ensure_envelope(result, tool_name=tool_name)

            if isinstance(result, dict):
                self._sandbox_note_result(tool_name, params, result)

            return result

        except asyncio.TimeoutError:
            logger.error(f"{tool.name} execution timed out after {timeout}s")
            result = failure.failure(
                f"Tool execution timed out after {timeout}s.",
                failure_class=failure.TIMEOUT,
                tool_name=tool_name,
                next_actions=[
                    "Retry ONCE with a narrower request (smaller scope, fewer items, "
                    "a shorter range) — an identical repeat will time out identically.",
                    f"The limit for this tool is {timeout}s; work that genuinely needs "
                    "longer must be split into smaller calls.",
                    "If a narrowed retry also times out, use a different tool or tell "
                    "the user this step exceeds the tool's limit.",
                ],
            )
            self._sandbox_note_result(tool_name, params, result)
            return result
        except Exception as e:
            logger.error(f"{tool.name} execution error: {e}", exc_info=True)
            result = failure.failure(e, tool_name=tool_name, exc=e)
            self._sandbox_note_result(tool_name, params, result)
            return result

    def _check_approval_gate(self, tool: Any, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Returns a short-circuit result dict if this call should pause for (or
        was just declined by) a human decision; None if execution should
        proceed normally. When approved, mutates `params` in place to add
        `_approval_decision`/`_approval_token` so the tool can read the
        resolved decision (e.g. a chosen MCQ option).

        Two triggers: the tool opting in (wants_approval) or the risk-based
        policy matching the tool name. Native opt-in wins for the prompt shape
        (custom approval_request + decision_type); policy-only tools get a
        generic accept/decline prompt.
        """
        from src.core.orchestration.approval_gate import evaluate_approval_gate
        from src.core.tools.tool_policy_manager import get_policy_manager

        native_opt_in = hasattr(tool, "wants_approval") and tool.wants_approval(params)
        policy_gated = get_policy_manager().requires_approval(tool.name)
        if not (native_opt_in or policy_gated):
            return None

        if native_opt_in:
            decision_type = getattr(tool, "approval_decision_type", "accept_decline")
            build_request = lambda: tool.approval_request(params)
        else:
            decision_type = "accept_decline"
            build_request = None  # gate uses its generic default prompt

        outcome = evaluate_approval_gate(
            tool.name, decision_type=decision_type, build_request=build_request, params=params
        )
        if outcome.short_circuit is not None:
            return outcome.short_circuit

        if outcome.decision is not None:
            # Approved — let the tool read the resolved decision (e.g. MCQ option).
            params["_approval_decision"] = outcome.decision
            params["_approval_token"] = outcome.token
        return None


class SkillExecutor(ExecutorStrategy):
    """
    Executor for BaseSkill instances (sub-agents).
    """

    def can_handle(self, tool: Any) -> bool:
        """Check if tool is a BaseSkill instance."""
        from src.core.skill_system.base_skill import BaseSkill
        return isinstance(tool, BaseSkill)

    async def execute(self, tool: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a BaseSkill.

        Skills have their own internal tool registries and execution logic.
        """
        from src.core.skill_system.base_skill import BaseSkill

        if not isinstance(tool, BaseSkill):
            return failure.failure(
                "Tool is not a BaseSkill instance", failure_class=failure.CAPABILITY_GAP
            )

        try:
            # Risk-based human-in-the-loop gate (config/tool_policies.yaml).
            gated = _policy_approval_gate(tool.name, params)
            if gated is not None:
                return gated

            # Skills expose as tools with execute() method
            result = await tool.execute(**params)
            return failure.ensure_envelope(result, tool_name=getattr(tool, "name", None))

        except Exception as e:
            logger.error(f"{tool.name} skill error: {e}", exc_info=True)
            return failure.failure(
                f"Skill execution failed: {e}", tool_name=getattr(tool, "name", None), exc=e
            )


class MCPExecutor(ExecutorStrategy, _TurnScopedState):
    """
    Executor for MCP server tools.
    """

    _turn_scoped_attrs = ("_browser_call_failure_counts",)

    def __init__(self):
        # browser_agent loop-breaker (issue #287): a signature -> consecutive-failure
        # counter for Playwright ("browser_*") calls only. Local reasoning-light models
        # were observed retrying the IDENTICAL failing call (same tool + same args)
        # 10+ times in a row without ever re-snapshotting or changing approach. Rather
        # than rely on the model to notice, block deterministically once a call has
        # failed once with the exact same arguments — the hybrid DOM+feedback probe
        # (evals/browser_hybrid_probe.py) proved banning on the FIRST bad result (not
        # the second) is what actually stops loops and forces a strategy change; the
        # original two-strikes threshold gave the model an extra free failed retry
        # before being forced to adapt (issue #317). Repeats of a call that SUCCEEDED
        # are never blocked (e.g. pressing PageDown twice to scroll further is
        # legitimate).
        #
        # SCOPED TO THE TURN (_TurnScopedState). This used to persist for the
        # process lifetime, justified as "fine for a single-user, single-browser
        # desktop app" — but the justification doesn't hold: the page changes
        # between turns. A click that failed at 9am against one page state stayed
        # permanently banned against the reloaded page that afternoon, which is
        # precisely the case where a retry SHOULD be allowed. Within a turn the
        # evidence is fresh and the ban is right; across turns it is stale.
        self._browser_call_failure_counts: Dict[str, int] = {}
        self._init_turn_scope()
        # Computer-use safeguards (issue #279): write-action approval gate,
        # per-site allowlist, and per-conversation action-budget kill-switch.
        from src.core.orchestration.browser_safety import BrowserSafetyState
        self._browser_safety = BrowserSafetyState()

    def can_handle(self, tool: Any) -> bool:
        """Check if tool is an MCPServerEntry."""
        # MCP tools are stored as dict metadata with server reference
        return isinstance(tool, dict) and 'mcp_server' in tool

    def _browser_safety_gate(self, tool_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Issue #279 computer-use safeguards for one browser_* call. Returns a
        short-circuit result dict (kill-switch block, or the approval gate's
        pending-pause/declined outcome) when the call must not proceed yet,
        else None to let execution continue.
        """
        from src.core.orchestration import browser_safety
        from src.core.orchestration.approval_gate import evaluate_approval_gate
        from src.core.orchestration.context import browser_scope_key

        conversation_id = browser_scope_key()

        if tool_name == "browser_navigate":
            self._browser_safety.note_navigation(conversation_id, params.get("url"))

        current_domain = self._browser_safety.current_domain(conversation_id)
        verdict = browser_safety.classify(tool_name, params, current_domain=current_domain)

        if verdict["requires_approval"]:
            def build_request() -> Dict[str, Any]:
                domain_note = f" on {current_domain}" if current_domain else ""
                return {
                    "prompt": (
                        f"browser_agent wants to run '{tool_name}'{domain_note} "
                        f"({verdict['reason']}). Allow it to proceed?"
                    ),
                    "title": "Browser action needs approval",
                }

            outcome = evaluate_approval_gate(tool_name, decision_type="accept_decline", build_request=build_request, params=params)
            if outcome.short_circuit is not None:
                # Still pending, or the user declined — don't count against the
                # action budget either way; nothing has actually run yet.
                return outcome.short_circuit

        # Approved (or never required approval) — this call is about to actually
        # run against the browser, so it counts against the kill-switch budget.
        count = self._browser_safety.record_action(conversation_id)
        if self._browser_safety.budget_exceeded(conversation_id):
            max_actions = browser_safety.get_browser_max_actions()
            logger.warning(f"[ MCP ] Browser action budget exceeded ({count}/{max_actions}) for conversation {conversation_id}")
            return failure.failure(
                f"Browser kill-switch: this task has already made {count} browser "
                f"actions, over the {max_actions}-action budget.",
                failure_class=failure.BLOCKED,
                tool_name=tool_name,
                next_actions=[
                    "No further browser actions will run for this task.",
                    "Summarize what you already found and deliver that — partial results "
                    "clearly labelled beat continuing to browse.",
                ],
                _terminal_budget=True,
            )

        return None

    def _annotate_browser_page_surface(
        self,
        tool_name: str,
        params: Dict[str, Any],
        processed_parts: List[Any],
    ) -> None:
        """Tag browser tool dumps with list vs detail surface + soft done-gate.

        Persona text alone does not stop models from quoting truncated inbox rows.
        Prepending `[PAGE SURFACE: ...]` and a list-preview CONTINUE nudge makes the
        constraint visible on every snapshot the model reads.
        """
        if not isinstance(processed_parts, list):
            return

        from src.core.orchestration.context import browser_scope_key
        from src.core.orchestration.page_surface import (
            classify_page_surface,
            extract_page_url,
            format_surface_banner,
        )

        conversation_id = browser_scope_key()
        chunks: List[str] = []
        for part in processed_parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            elif isinstance(part, str):
                chunks.append(part)
        blob = "\n".join(chunks)

        # Prefer the URL Playwright reports after the action (redirects), not the
        # pre-nav target — e.g. theknot.com → /dashboard or /inbox/conversations/<id>.
        url = extract_page_url(blob)
        if not url and tool_name == "browser_navigate" and isinstance(params.get("url"), str):
            url = params.get("url")
        if not url:
            url = self._browser_safety.current_url(conversation_id)

        if url:
            self._browser_safety.note_navigation(conversation_id, url)

        surface = classify_page_surface(url, blob)
        banner = format_surface_banner(surface, url)
        # Avoid double-tagging if a prior hop already injected the same banner.
        if any(isinstance(p, dict) and banner.split("\n", 1)[0] in str(p.get("text", "")) for p in processed_parts):
            return surface
        processed_parts.insert(0, {"type": "text", "text": banner})
        logger.info(f"[ MCP ] browser surface={surface.value} tool={tool_name} url={url}")
        return surface

    async def execute(self, tool: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an MCP tool.
        """
        if not isinstance(tool, dict) or 'mcp_server' not in tool:
            return {"success": False, "error": "Tool is not an MCP tool"}

        try:
            from src.infrastructure.tools.execution.snapshot_pruner import prune_snapshot, prune_uia_tree, render_set_of_mark
            server = tool['mcp_server']
            tool_name = tool['name']

            # Risk-based human-in-the-loop gate (config/tool_policies.yaml).
            # MCP tools (send_email, delete_*, execute_sql, ...) pause here on
            # first call and resume after the user approves. See approval_gate.py.
            gated = _policy_approval_gate(tool_name, params)
            if gated is not None:
                return gated

            # --- UI Automation Parameter Sanitization ---
            # 1. Coerce string IDs to integers for certain Windows tools if they are purely numeric
            ui_tools_needing_int = ["Click", "Move", "Type", "Scroll", "ui_click", "ui_type", "ui_scroll"]
            if tool_name in ui_tools_needing_int:
                for param_key in ["label", "id"]:
                    if param_key in params and isinstance(params[param_key], str) and params[param_key].isdigit():
                        params[param_key] = int(params[param_key])
                        logger.debug(f"Coerced {param_key} to integer for {tool_name}")
            
            # 2. Coerce windowHandle to string (Required by some .NET MCP servers for stability)
            if "windowHandle" in params and params["windowHandle"] is not None:
                if not isinstance(params["windowHandle"], str):
                    params["windowHandle"] = str(params["windowHandle"])
                    logger.debug(f"Coerced windowHandle to string for {tool_name}")

            # --------------------------------------------

            params = _sanitize_mcp_params(params)
            params = _coerce_mcp_search_params(tool_name, params)
            params = _normalize_playwright_target_params(tool_name, params)
            logger.info(f"[ MCP ] Executing '{tool_name}' with params: {json.dumps(params)}")

            # Computer-use safeguards (issue #279): read-only by default, confirm
            # writes. Runs before the loop-breaker so a declined/pending write
            # never counts as a failed attempt.
            if tool_name.startswith("browser_"):
                blocked = self._browser_safety_gate(tool_name, params)
                if blocked is not None:
                    return blocked

            # Loop-breaker: block any repeat of a Playwright call that has already
            # failed once with these exact args (#287, tightened to first-failure in #317
            # to match the probe that proved this stops loops faster than two-strikes).
            call_sig = None
            if tool_name.startswith("browser_"):
                self._reset_state_on_new_turn()
                call_sig = f"{tool_name}:{json.dumps(params, sort_keys=True, default=str)}"
                fail_count = self._browser_call_failure_counts.get(call_sig, 0)
                if fail_count >= int(limits.get("repeat_failure_block_after")):
                    logger.warning(f"[ MCP ] Blocking repeated failing call: {call_sig}")
                    # Vision-assist fallback (#288): the text/SoM loop is stuck on this
                    # exact target — only worth the model-swap cost for target-bearing
                    # calls (clicks/typing), not e.g. repeated navigate/wait_for.
                    vision_hint = None
                    if "target" in params:
                        vision_hint = await _vision_assist_resolve_target(server, call_sig)
                    actions = [
                        "Call browser_snapshot (or browser_wait_for) to re-read the CURRENT "
                        "page state — your view of the page is stale.",
                        "Then target a different element, or take a different route to the goal.",
                    ]
                    if vision_hint:
                        actions.insert(0, f"Vision-assist suggests: {vision_hint}")
                    return failure.failure(
                        f"'{tool_name}' with these exact arguments has already failed "
                        f"{fail_count} time(s) in a row with the same error.",
                        failure_class=failure.BLOCKED,
                        tool_name=tool_name,
                        next_actions=actions,
                    )

            # Call the MCP server tool, bounded so a hung MCP server can't stall the loop
            timeout = get_tool_timeout(tool_name)
            if tool_name.startswith('browser_'):
                from src.core.orchestration.context import record_browser_mcp_call
                record_browser_mcp_call()
            result: list = await asyncio.wait_for(server.call_tool(tool_name, params), timeout=timeout)

            # Process and serialize results
            processed_parts = []
            has_error = False
            total_original = 0
            total_kept = 0
            is_windows_snapshot = (tool_name == "Snapshot")
            is_browser = tool_name.startswith("browser_")

            for idx, item in enumerate(result):
                # MCPServerEntry returns model_dump() dictionaries. Normalize text
                # blocks so they receive the same hydration/pruning as SDK objects.
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    from mcp.types import TextContent
                    item = TextContent.model_validate({**item, "type": "text"})
                # 1. Extract and prune text if it's a snapshot
                if hasattr(item, "text") and isinstance(item.text, str):
                    if is_windows_snapshot:
                        pruned, orig, kept = prune_uia_tree(item.text)
                        total_original += orig
                        total_kept += kept
                        if hasattr(item, "model_copy"):
                            item = item.model_copy(update={"text": pruned})
                        else:
                            item.text = pruned
                    elif is_browser:
                        # Pruning for browser snapshots (file links or inline)
                        text = item.text
                        pruned_text = text
                        som_menu = ""
                        file_link_match = re.search(r"\[Snapshot\]\(([^)]+\.yml)\)", text)
                        if file_link_match:
                            relative_path = file_link_match.group(1).replace("\\", os.sep).replace("/", os.sep)
                            try:
                                abs_path = os.path.realpath(os.path.join(PROJECT_ROOT, relative_path))
                                if os.path.commonpath([os.path.realpath(PROJECT_ROOT), abs_path]) != os.path.realpath(PROJECT_ROOT):
                                    raise ValueError("Snapshot path is outside the project")
                                with open(abs_path, "r", encoding="utf-8") as f:
                                    yaml_content = f.read()
                                pruned, orig, kept = prune_snapshot(yaml_content)
                                total_original += orig
                                total_kept += kept
                                som_menu = render_set_of_mark(pruned)
                                pruned_text = re.sub(
                                    r"^ *- \[Snapshot\]\([^)]+\.yml\)",
                                    lambda _: f"### Pruned Snapshot ({kept}/{orig} lines)\n```yaml\n{pruned}\n```",
                                    text,
                                    flags=re.MULTILINE,
                                )
                            except (OSError, UnicodeError, ValueError) as exc:
                                logger.warning("[ MCP ] Snapshot hydration failed: %s", type(exc).__name__)
                                pruned_text += (
                                    "\n[OBSERVATION INCOMPLETE] Snapshot file could not be read. "
                                    "Call browser_snapshot without a filename to obtain a fresh inline tree; "
                                    "this file link is not evidence of visible page contents."
                                )
                        else:
                            inline_match = re.search(r"```yaml\n(.*?)```", text, re.DOTALL)
                            if inline_match:
                                yaml_content = inline_match.group(1)
                                pruned, orig, kept = prune_snapshot(yaml_content)
                                total_original += orig
                                total_kept += kept
                                som_menu = render_set_of_mark(pruned)
                                pruned_text = text.replace(inline_match.group(0), f"```yaml\n{pruned}\n```")

                        # Prepend the explicit numbered Set-of-Mark menu FIRST so the model
                        # picks an interactive target by number/ref immediately, and so
                        # downstream context-budget truncation of the raw YAML tree never cuts off the SoM menu.
                        if som_menu:
                            pruned_text = f"{som_menu}\n\n{pruned_text}"

                        # Enforce max_char_limit on browser outputs as well so massive pages
                        # don't bypass context-budgeting and blow the model context window.
                        max_char_limit = get_model_capability_registry().get_active_max_tool_result_chars()
                        if len(pruned_text) > max_char_limit:
                            original_len = len(pruned_text)
                            pruned_text = (
                                pruned_text[:max_char_limit]
                                + f"\n\n... [TRUNCATED {original_len - max_char_limit} characters to prevent context window flooding. Please refine your query, filter results, or use pagination/specific parameters if possible] ..."
                            )

                        if hasattr(item, "model_copy"):
                            item = item.model_copy(update={"text": pruned_text})
                        else:
                            item.text = pruned_text
                    else:
                        # General text truncation — limit scales with the active model's context window
                        max_char_limit = get_model_capability_registry().get_active_max_tool_result_chars()
                        if len(item.text) > max_char_limit:
                            original_len = len(item.text)
                            truncated_text = item.text[:max_char_limit] + f"\n\n... [TRUNCATED {original_len - max_char_limit} characters to prevent context window flooding. Please refine your query, filter results, or use pagination/specific parameters if possible] ..."
                            if hasattr(item, "model_copy"):
                                item = item.model_copy(update={"text": truncated_text})
                            else:
                                item.text = truncated_text
                            logger.warning(f"[ MCP ] Tool '{tool_name}' output truncated from {original_len} to {max_char_limit} chars.")
                    result[idx] = item
                elif isinstance(item, dict) and "text" in item and isinstance(item["text"], str):
                    max_char_limit = get_model_capability_registry().get_active_max_tool_result_chars()
                    if len(item["text"]) > max_char_limit:
                        original_len = len(item["text"])
                        item["text"] = item["text"][:max_char_limit] + f"\n\n... [TRUNCATED {original_len - max_char_limit} characters to prevent context window flooding. Please refine your query, filter results, or use pagination/specific parameters if possible] ..."
                        logger.warning(f"[ MCP ] Tool '{tool_name}' dict output truncated from {original_len} to {max_char_limit} chars.")
                    result[idx] = item
                elif isinstance(item, str):
                    max_char_limit = get_model_capability_registry().get_active_max_tool_result_chars()
                    if len(item) > max_char_limit:
                        original_len = len(item)
                        item = item[:max_char_limit] + f"\n\n... [TRUNCATED {original_len - max_char_limit} characters to prevent context window flooding] ..."
                        logger.warning(f"[ MCP ] Tool '{tool_name}' str output truncated from {original_len} to {max_char_limit} chars.")
                    result[idx] = item

                # 2. Extract text for error detection
                text_to_check = ""
                if hasattr(item, "text") and isinstance(item.text, str):
                    text_to_check = item.text
                elif isinstance(item, dict) and "text" in item:
                    text_to_check = str(item["text"])
                elif not hasattr(item, "model_dump") and not hasattr(item, "dict") and not hasattr(item, "to_dict"):
                    text_to_check = str(item)

                if text_to_check:
                    # Web scraping tools return arbitrary text that often includes these keywords 
                    # as part of legitimate page content. Bypass scanner to prevent false positives.
                    skip_error_scan = server.name in ["brave-search", "fetch"] or \
                                    tool_name.startswith(("fetch_", "brave_"))

                    if not skip_error_scan:
                        error_keywords = [
                            "validation error", "error:", "### error\n", "internal error", "failed to execute",
                            "an error occurred invoking", '\"success\":false', '\"success\": false', 
                            '\'success\': False', '\"error\"'
                        ]
                        if any(k in text_to_check.lower() for k in error_keywords) or has_error:
                            has_error = True
                        
                        # Add a helpful hint for Chad if the error is generic
                        generic_error_hint = ""
                        if "an error occurred invoking" in text_to_check.lower():
                            if tool_name == "ui_find":
                                generic_error_hint = " (HINT: This usually means the windowHandle is invalid, the target window was closed, or there's a permission issue. Try refreshing with window_management(action='list').)"
                            elif tool_name in ["ui_click", "ui_type", "ui_scroll"]:
                                generic_error_hint = " (HINT: Ensure the target window is in focus and the elementId is still valid. If you just opened the app, you may need to wait 1-2 seconds.)"
                            elif tool_name == "Snapshot":
                                generic_error_hint = " (HINT: UI Snapshot failed. If this app is running as Administrator, Chad needs to be elevated too. Otherwise, check if the window is minimized.)"
                            else:
                                generic_error_hint = " (HINT: This generic error often indicates a crash in the MCP server or an invalid parameter type/value.)"
                        elif is_browser and has_error and any(
                            k in text_to_check.lower()
                            for k in ("ref not found", "not found in the current", "no element", "stale")
                        ):
                            # Playwright's own error is terse ("Ref e13 not found") and gives the
                            # model nothing to correct on — the hybrid probe's harness always paired
                            # a stale-ref failure with live ref examples (#317). Production can't cheaply
                            # replay a fresh snapshot here, but it CAN stop the model from guessing
                            # another stale ref from its (now outdated) mental map of the page.
                            generic_error_hint = (
                                " (HINT: this ref is stale — the page re-rendered or you're working "
                                "from an old snapshot. Call browser_snapshot now and pick a ref from "
                                "the NEW result. Do not guess another ref from memory.)"
                            )
                        
                        if generic_error_hint and generic_error_hint not in text_to_check:
                            text_to_check += generic_error_hint
                            # Update the item so the hint is included in the response
                            if hasattr(item, "model_copy"):
                                item = item.model_copy(update={"text": text_to_check})
                            elif isinstance(item, dict):
                                item["text"] = text_to_check

                        if has_error:
                            logger.error(f"[ MCP ] Tool '{tool_name}' returned internal error: {text_to_check}")


                # 3. Convert to serializable dict
                if hasattr(item, "model_dump"):
                    processed_parts.append(item.model_dump())
                elif hasattr(item, "dict"):
                    processed_parts.append(item.dict())
                elif hasattr(item, "to_dict"):
                    processed_parts.append(item.to_dict())
                elif isinstance(item, dict):
                    processed_parts.append(item)
                else:
                    processed_parts.append(str(item))

            # 3. Handle Empty Results
            if not processed_parts:
                logger.warning(f"[ MCP ] Tool '{tool_name}' returned empty result.")
            else:
                # Log a snippet of the result
                res_str = str(processed_parts)[:500]
                if has_error:
                    logger.error(f"[ MCP ] Tool '{tool_name}' FAILED with result: {res_str}...")
                else:
                    logger.info(f"[ MCP ] Tool '{tool_name}' result: {res_str}...")
                
            # 4. Post-Process Snapshot for Focus Validation
            if is_windows_snapshot and processed_parts:
                full_text = "\n".join([str(p.get('text', p)) if isinstance(p, dict) else str(p) for p in processed_parts])
                # Check if we were looking for a window but another one is focused
                if "Focused Window" in full_text and "Settings" in full_text:
                     # Simple heuristic: if 'Settings' is in opened windows but NOT in focused window
                     focused_line = [l for l in full_text.split("\n") if "Focused Window:" in l]
                     if focused_line and "Settings" not in focused_line[0]:
                         processed_parts.append({
                             "type": "text", 
                             "text": "CRITICAL WARNING: The 'Settings' window is NOT in focus. UI automation tools (Click, Type) will fail unless you bring it to focus first using App(mode='switch', name='Settings')."
                         })
                         has_error = True # Force the model to handle this

            # Log pruning stats
            if total_original > 0:
                savings_pct = round((1 - total_kept / total_original) * 100)
                logger.info(f"[MCPExecutor] {tool_name}: {total_original} -> {total_kept} lines ({savings_pct}% pruned)")

            # Update the loop-breaker counter (#287): failures accumulate per exact
            # signature, any success (or a different signature) clears it.
            if call_sig is not None:
                if has_error:
                    self._browser_call_failure_counts[call_sig] = self._browser_call_failure_counts.get(call_sig, 0) + 1
                else:
                    self._browser_call_failure_counts.pop(call_sig, None)

            metadata = {}
            if is_windows_snapshot: metadata["_windows_tool"] = True
            if is_browser: metadata["_browser_tool"] = True

            # List-vs-detail surface tag + soft done-gate for inbox/search previews.
            if is_browser and processed_parts and not has_error:
                try:
                    surface = self._annotate_browser_page_surface(tool_name, params, processed_parts)
                    from src.core.orchestration.page_surface import PageSurface
                    if surface == PageSurface.PARKED_DOMAIN:
                        has_error = True
                        logger.warning(f"[ MCP ] Tool '{tool_name}' landed on a PARKED_DOMAIN page — flagging has_error=True.")
                except Exception as annotate_err:
                    logger.debug(f"[ MCP ] surface annotate skipped: {annotate_err}")
            
            # --- Ephemeral/Terminal Action Acknowledgment ---
            # These actions are definitive and often complete the user's intent.
            # Marking them as 'terminal' allows the orchestrator to conclude the loop.
            terminal_tools = [
                "open_browser", "open_browser_app", "SystemApp",
                "send_telegram_message", "send_email", "create_gmail_draft"
            ]
            if tool_name in {"Click", "Type", "KeyPress", "Scroll", "Move", "ui_click", "ui_type", "ui_scroll"} and not has_error:
                processed_parts.append({"type": "text", "text": (
                    "[ACTION ACKNOWLEDGED] The input operation succeeded on the returned target. "
                    "Compare its actual name/type with your intended control; a successful click "
                    "does not prove the requested application outcome. Obtain one fresh, relevant "
                    "state observation before claiming completion."
                )})
            if tool_name in terminal_tools and not has_error:
                metadata["terminal"] = True
                logger.info(f"[ MCP ] Marked tool '{tool_name}' as terminal.")
                
                # Append a hint for the model to prevent over-verification loops
                hint = f"\n[SUCCESS]: {tool_name} was executed successfully. This action is definitive. " \
                       "If this was part of a multi-step task, proceed to the next step. " \
                       "If this was the final action, conclude the turn by reporting success to the user."
                
                if isinstance(processed_parts, list):
                    processed_parts.append({"type": "text", "text": hint})
                else:
                    processed_parts = str(processed_parts) + hint
            # ------------------------------------------------

            return {
                "success": not has_error, 
                "result": processed_parts, 
                "_token_est": total_kept if total_kept > 0 else 0,
                **metadata
            }

        except (TimeoutError, asyncio.TimeoutError) as e:
            name = tool.get("name", "unknown")
            logger.error(f"{name} MCP timeout: {e}")
            return failure.failure(
                f"MCP tool '{name}' timed out.",
                failure_class=failure.TIMEOUT,
                tool_name=name,
                next_actions=[
                    "Retry ONCE with a narrower request — an identical repeat will "
                    "time out identically.",
                    "If it times out again, the MCP server is likely wedged: use a "
                    "different tool or report it.",
                ],
            )
        except Exception as e:
            name = tool.get("name", "unknown")
            logger.error(f"{name} MCP error: {e}", exc_info=True)
            return failure.failure(
                f"MCP tool '{name}' failed: {e}", tool_name=name, exc=e
            )



class NullExecutor(ExecutorStrategy):
    """
    Fallback executor for when no other strategy matches.
    Prevents NoneType errors in the registry.
    """
    def can_handle(self, tool: Any) -> bool:
        return True # Always matches as a final fallback

    async def execute(self, tool: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = getattr(tool, 'name', str(tool))
        logger.error(f"No execution strategy found for tool '{tool_name}'.")
        return failure.failure(
            f"No execution strategy found for tool '{tool_name}' — it is registered "
            f"but not runnable.",
            failure_class=failure.CAPABILITY_GAP,
            tool_name=tool_name,
            next_actions=[
                "Do NOT retry this tool — it cannot execute in this build.",
                "Use a different tool for this step, or tell the user the capability "
                "is unavailable and finish what you can.",
            ],
        )


class ExecutionRoutingEngine:
    """
    Logic engine for routing tool execution to the appropriate strategy.
    Handles the decision between Local, Skill, and MCP execution environments.
    """

    def __init__(self):
        self.executors = [
            LocalExecutor(),
            SkillExecutor(),
            MCPExecutor(),
            NullExecutor() # Safety net
        ]

    def route_to_executor(self, tool: Any) -> Optional[ExecutorStrategy]:
        """
        Route a tool to its appropriate execution strategy.

        Args:
            tool: The tool instance

        Returns:
            Executor strategy or None if no executor can handle it
        """
        for executor in self.executors:
            if executor.can_handle(tool):
                return executor
        return None
