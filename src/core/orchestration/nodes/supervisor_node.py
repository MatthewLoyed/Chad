import logging
from typing import Dict, Any, Callable, Optional
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig

from src.core.orchestration.plan_service import PlanService
from src.core.orchestration.scout_evidence import render_evidence
from src.core.orchestration.plan_step_resources import (
    SKILL_META_TOOLS,
    classify_step_resources,
    step_references_skill,
)
from src.infrastructure.tools.delegate_routing import (
    delegate_hints_for_step_tools,
    is_supervisor_delegate_only,
)
from src.infrastructure.tools.web_search_routing import (
    DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER,
    BROWSER_AGENT_SUPERVISOR_ROSTER,
)
from src.infrastructure.tools.file_routing import (
    TRAVERSAL_AGENT_SUPERVISOR_ROSTER,
)
from src.infrastructure.tools.mcp_roster import build_active_mcp_roster
from src.infrastructure.tools.capability_directory import build_capability_directory
from src.core.tools import limits, reflect

logger = logging.getLogger("ChadSupervisor")

_STEP_MGMT_TOOLS = frozenset({"complete_step", "fail_step", "pause_for_input"})

# Per-step supervisor tool-turns (not wall-clock). Soft = nudge; hard = force a
# different approach on the SAME step — never scout/replan just for stalling.
# 6 was too low: SAFETY LOCK / wrong-tool retries burned the budget before a
# real approach change (HITL 2026-07-24 wedding plan→build).
# Values now live in src/core/tools/limits.py so every boundary is in one place
# and tunable from config.yaml without a code change.


def _step_turn_soft() -> int:
    return int(limits.get("step_turns_soft"))


def _step_turn_hard() -> int:
    return int(limits.get("step_turns_max"))


def _recent_tool_error_hints(messages: list, *, limit: int = 8) -> list[str]:
    """Pull short error snippets from recent ToolMessages for recovery guidance."""
    from langchain_core.messages import ToolMessage

    hints: list[str] = []
    for msg in reversed(messages or []):
        if not isinstance(msg, ToolMessage):
            continue
        raw = str(getattr(msg, "content", "") or "")
        low = raw.lower()
        if not any(tok in low for tok in ("error", "fail", "safety lock", "denied", "invalid", "not found", "missing")):
            continue
        name = getattr(msg, "name", None) or "tool"
        snippet = " ".join(raw.split())
        if len(snippet) > 220:
            snippet = snippet[:217] + "..."
        hints.append(f"{name}: {snippet}")
        if len(hints) >= limit:
            break
    hints.reverse()
    return hints


def _build_step_recovery_feedback(
    step: Dict[str, Any],
    messages: list,
    *,
    reason: str,
) -> str:
    """Actionable recovery text — push a different approach, not a full replan."""
    step_id = step.get("id") or "?"
    desc = (step.get("description") or "").strip()
    hints = _recent_tool_error_hints(messages)
    lines = [
        f"Step '{step_id}' is stalled ({reason}). Stay on THIS step — do NOT create_plan / re-scout.",
        f"Goal of this step: {desc}" if desc else f"Goal of this step: {step_id}",
        "Try a DIFFERENT approach than the last few tool calls.",
    ]
    if hints:
        lines.append("Recent tool errors (learn from these):")
        lines.extend(f"- {h}" for h in hints[-5:])
    joined = " ".join(hints).lower()
    if "safety lock" in joined or "approval_token" in joined or "propose_file_plan" in joined:
        lines.append(
            "Hint: skip file_manager folder/approval flows — write the deliverable with "
            "write_file or build_html_file (defaults to artifacts/) or delegate_to_code_agent."
        )
    elif "overwrite" in joined or "already exists" in joined:
        lines.append("Hint: retry write_file/build_html_file with overwrite=true, or a new filename.")
    elif "delegate_to_code_agent" in joined or "code_agent" in joined:
        lines.append(
            "Hint: if the specialist already produced content, complete_step and move on, "
            "or call write_file/build_html_file yourself with that content."
        )
    else:
        lines.append(
            "Hint: switch tool family (different delegate, write_file/build_html_file, or "
            "ask only if truly blocked via pause_for_input)."
        )
    lines.append("Then call complete_step when the step's deliverable exists.")
    return "\n".join(lines)


def _collapse_repeated_failures(messages: list) -> list:
    """Return a COPY of ``messages`` with repeat failures of the same kind collapsed.

    Attempts are cheap on local inference; context is not. Without this, five tries
    at a wedged tool cost five full failure envelopes — the same class line and the
    same NEXT_ACTIONS block, repeated — and the plan, the goal, and the user's actual
    request get pushed out of the model's effective window. The model doesn't stop
    because it ran out of attempts; it stops because it can no longer see the task.

    Policy: keep the FIRST and the MOST RECENT occurrence of each failure signature
    (class + tool) in full. Everything between becomes a one-line counter. The first
    is kept because it usually carries the most specific cause; the most recent
    because it reflects current state. The counter itself is useful signal — "this
    has now failed 4 times identically" is exactly the cue to change approach, and
    it is far cheaper than showing the model four copies of the same text.

    Never mutates the input list: ``messages`` is the persisted graph history, and
    rewriting it would corrupt the transcript the UI and memory both read.
    """
    from src.core.tools import failure as _failure

    positions: Dict[str, list] = {}
    for idx, msg in enumerate(messages or []):
        if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
            continue
        sig = _failure.failure_signature(msg.content)
        if sig:
            positions.setdefault(sig, []).append(idx)

    # Only signatures seen 3+ times have anything worth collapsing (first + last
    # are always kept, so 2 occurrences would collapse nothing).
    collapse_at: Dict[int, tuple] = {}
    for sig, idxs in positions.items():
        if len(idxs) < 3:
            continue
        for rank, idx in enumerate(idxs[1:-1], start=2):
            collapse_at[idx] = (sig, rank, len(idxs))

    if not collapse_at:
        return messages

    out = list(messages)
    for idx, (sig, rank, total) in collapse_at.items():
        cls, _, tool = sig.partition(":")
        who = tool or "this tool"
        original = out[idx]
        out[idx] = ToolMessage(
            content=(
                f"[TOOL_DATA]\n[attempt {rank} of {total}: {who} failed with the same "
                f"'{cls}' failure again — identical detail omitted. Repeating this call "
                f"is not working; change approach.]"
            ),
            tool_call_id=getattr(original, "tool_call_id", "") or "",
            name=getattr(original, "name", None),
        )
    logger.info(
        "[ SUPERVISOR ] Collapsed %d repeated failure message(s) to protect context.",
        len(collapse_at),
    )
    return out


def _inject_turn_context_into_last_turn(messages: list, turn_context: str) -> list:
    """Return a COPY of ``messages`` with per-turn context appended to the last human
    turn.

    ``turn_context`` bundles everything that changes turn-to-turn — execution-plan
    status, offline-tool notices, and personal knowledge (graph + activity RAG).

    Kept out of the system prompt on purpose: the system prompt precedes the whole
    conversation history, so per-turn-volatile content there forces the local model
    to re-prefill the entire history every turn (KV-cache miss → slow TTFT). Riding
    it in the trailing user turn means it lands in the uncached tail that has to be
    processed anyway, while the system prompt + prior history stay a cacheable prefix.
    The trailing position also gives plan status recency, which aids step-adherence.

    Never mutates the input list — ``messages`` is the persisted graph history, and
    mutating it would permanently accumulate these blocks into the transcript.
    """
    if not turn_context or not messages:
        return messages
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human":
            content = m.content if isinstance(m.content, str) else str(m.content)
            new_content = (
                f"{content}\n\n---\n"
                f"[Current turn context — plan status and personal knowledge provided "
                f"by the system; use only what's relevant:]{turn_context}"
            )
            return messages[:i] + [HumanMessage(content=new_content)] + messages[i + 1:]
    return messages


def _append_tool_if_missing(tool_registry, tools: list, bound_names: set, tool_name: str) -> None:
    if tool_name in bound_names:
        return
    t = tool_registry.get_langchain_tool_by_name(tool_name)
    if t:
        tools.append(t)
        bound_names.add(tool_name)


def _collect_skill_catalog_names(skill_loader) -> set:
    return {s["name"] for s in skill_loader.list_skills()}


def format_active_skill_stub(skill_name: str, subagent: Optional[str], requires_mcp: bool = False) -> str:
    """Prompt stub after load_skill.

    Skills with an explicit ``subagent`` frontmatter field are specialist-owned
    (e.g. homework_assistant → homework_assistant_agent, frontend-design →
    code_agent). Skills without one (finance, financial_visualization, canvas,
    kroki, presenton, typst, canva, excalidraw, …) stay on the supervisor —
    the load_skill ToolMessage already carries the full SOP.
    """
    if subagent:
        owner = subagent if str(subagent).endswith("_agent") else f"{subagent}_agent"
        return (
            f"\n\n### 🟢 ACTIVE SKILL: {skill_name}\n"
            f"Skill is activated. Do NOT implement this yourself — call "
            f"`delegate_to_{owner}` with a clear task. The specialist receives "
            f"the full skill SOP.\n"
        )
    if requires_mcp:
        return (
            f"\n\n### 🟢 ACTIVE SKILL: {skill_name}\n"
            f"Skill is activated. Follow the SOP from the load_skill result yourself "
            f"using `call_mcp_tool` for the connected MCP server. Do not delegate unless "
            f"the skill explicitly says to.\n"
        )
    return (
        f"\n\n### 🟢 ACTIVE SKILL: {skill_name}\n"
        f"Skill is activated. Follow the SOP from the skill guide yourself using your bound "
        f"native tools directly. Call the tools directly (do NOT use `call_mcp_tool` for native tools). "
        f"Do not delegate unless the skill explicitly says to.\n"
    )


async def _load_skill_catalog():
    try:
        from src.core.skill_system.skill_loader import get_skill_loader
        loader = get_skill_loader()
        if loader.is_empty:
            await loader.initialize()
        return loader, _collect_skill_catalog_names(loader)
    except Exception as exc:
        logger.debug(f"[ SUPERVISOR ] Skill catalog unavailable: {exc}")
        return None, set()


def make_supervisor_node(model: Any, sub_agents_dict: dict, tool_registry: Any, handoff_tools: list) -> Callable:
    """Factory creating the supervisor node function."""
    
    async def call_supervisor(state: Dict[str, Any], config: RunnableConfig):
        """The supervisor node: Decides whether to call a tool or finish."""
        messages = state["messages"]
        iteration = state.get("iteration_count", 0) + 1  # 👈 track it
        plan = state.get("active_plan") or []
        
        # --- DYNAMIC BREADTH INJECTION ---
        # Get tool schemas found by RAG for this turn
        rag_schemas = state.get("tools") or []
        rag_tool_names = []
        for s in rag_schemas:
            if "function" in s and "name" in s["function"]:
                rag_tool_names.append(s["function"]["name"])
            elif "name" in s:
                rag_tool_names.append(s["name"])

        is_fixed_lane = state.get("category") in ("telegram_agent", "feed")
        if state.get("category") == "feed":
            from src.core.discovery.feed_context import FEED_TOOL_NAMES
            rag_tool_names = [name for name in rag_tool_names if name in FEED_TOOL_NAMES]
        
        # Resolve names to actual BaseTool instances for binding
        direct_tools = []
        offline_tools = []
        logger.info(f"[ SUPERVISOR ] Turn starting. Tools in state: {len(rag_tool_names)} tool(s)")
        logger.debug(f"[ SUPERVISOR ] Tool names in state: {rag_tool_names}")
        
        for name in rag_tool_names:
            if (
                not is_fixed_lane
                and is_supervisor_delegate_only(name, tool_registry._categories, tool_registry._tool_sources)
            ):
                logger.debug(f"[ SUPERVISOR ] Skipping delegate-only tool '{name}' from state bindings.")
                continue
            t = tool_registry.get_langchain_tool_by_name(name)
            if t:
                direct_tools.append(t)
            else:
                logger.warning(f"[ SUPERVISOR ] DROP: Tool '{name}' missing from local registry (offline MCP server or stale database entry).")
                offline_tools.append(name)

        # Always bind tools explicitly assigned to the active plan step
        if plan and not is_fixed_lane:
            active_step_obj = next(
                (s for s in plan if s.get("status") in ["pending", "failed", "running"]), None
            )
            if active_step_obj:
                for step_tool in (active_step_obj.get("tools") or []):
                    if not any(t.name == step_tool for t in direct_tools):
                        t = tool_registry.get_langchain_tool_by_name(step_tool)
                        if t:
                            direct_tools.append(t)
                            logger.info(f"[ SUPERVISOR ] Bound explicit plan-step tool: {step_tool}")
        
        # Always inject step-management tools when a plan is active (not on Telegram pocket remote)
        step_mgmt_tools = []
        if state.get("active_plan") and not is_fixed_lane:
            for tool_name in _STEP_MGMT_TOOLS:
                t = tool_registry.get_langchain_tool_by_name(tool_name)
                if t:
                    step_mgmt_tools.append(t)
                else:
                    logger.warning(f"[ SUPERVISOR ] Step tool '{tool_name}' not found in registry.")

        effective_handoffs = [] if is_fixed_lane else handoff_tools
        bound_tool_names = {t.name for t in effective_handoffs + step_mgmt_tools + direct_tools}

        # Keep discovery usable even when the selector omitted the relevant tools.
        if not is_fixed_lane:
            for meta_tool in ("search_for_tool", "list_skills", "load_skill"):
                _append_tool_if_missing(tool_registry, direct_tools, bound_tool_names, meta_tool)

        # Skill catalog — skills are usable via load_skill even when RAG skipped them.
        skill_loader = None
        skill_catalog_names: set = set()
        if not is_fixed_lane:
            skill_loader, skill_catalog_names = await _load_skill_catalog()

        plan = state.get("active_plan") or []
        plan_step_tools: list = []
        for step in plan:
            plan_step_tools.extend(step.get("tools") or [])

        if plan and skill_catalog_names and step_references_skill(plan_step_tools, skill_catalog_names):
            for meta_tool in SKILL_META_TOOLS:
                _append_tool_if_missing(tool_registry, direct_tools, bound_tool_names, meta_tool)

        # Does the active plan step call for research? (Used only to add a mandatory directive
        # below.) deep_research_agent (#510) is the sole research delegate — there's no serial
        # per-item path for the model to fall back to.
        _active_step_early = next(
            (s for s in plan if s.get("status") in ["pending", "failed", "running"]), None
        ) if plan else None
        step_requires_parallel = bool(
            _active_step_early
            and "delegate_to_deep_research_agent" in (_active_step_early.get("tools") or [])
        )

        # Combine Handoffs (Depth) + Step Tools + RAG Tools (Breadth) + skill meta tools
        # Telegram pocket remote: fixed direct tools only — no specialist handoffs.
        handoff_names = [t.name for t in effective_handoffs]
        current_turn_tools = effective_handoffs + step_mgmt_tools + direct_tools
        logger.info(
            f"[ SUPERVISOR ] Bound {len(current_turn_tools)} tools "
            f"({len(effective_handoffs)} specialists, {len(step_mgmt_tools)} step tools, {len(direct_tools)} direct tools)"
        )
        logger.debug(
            f"[ SUPERVISOR ] Final Bound Tools: Specialists={handoff_names}, "
            f"StepTools={[t.name for t in step_mgmt_tools]}, Atomics={[t.name for t in direct_tools]}"
        )
        
        # Bind tools fresh for this turn to the model
        supervisor_model = model.bind_tools(current_turn_tools)
        
        # --- PROMPT MERGING (Cloud Persona + Local Logic) ---
        # 1. Start with the Cloud-Master Persona (Supabase Rules)
        cloud_persona = state.get("system_instruction", "")

        # 2. Append Local Orchestration Logic
        # NOTE: supervisor_routing and supervisor_skill are already present in cloud_persona
        # (assembled by SystemPromptBuilder from Supabase persona_config, scope=supervisor).
        # We only add DYNAMIC content here that changes per-turn: agent roster, skill catalog,
        # plan state, and completion sentinels. Do NOT re-fetch static persona prompts here.
        # Telegram agent uses its own telegram-scoped persona — skip desktop supervisor doctrine.
        orchestration_logic = ""

        # Inject active specialist roster — only when agents are actually compiled in.
        # When no subagents are active, omit the block entirely (no noise).
        if sub_agents_dict and not is_fixed_lane:
            _AGENT_DESCRIPTIONS = {
                "ui_automation_agent": "Clicks/types in local Windows apps and executes UI automation flows.",
                "browser_agent": BROWSER_AGENT_SUPERVISOR_ROSTER,
                "code_agent": (
                    "Light in-turn coding and UI artifacts (one-shot HTML/pages via "
                    "build_html_file, small scripts). Deep / multi-file work → Chad Code, "
                    "not this agent — never implement code on the supervisor yourself."
                ),
                "system_agent": "Manages local files, checks system stats, and adjusts OS settings.",
                "traversal_agent": TRAVERSAL_AGENT_SUPERVISOR_ROSTER,
                "deep_research_agent": DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER,
                "canvas_agent": (
                    "Canvas LMS specialist: explores courses and modules, searches and downloads "
                    "lecture slides/files, checks assignments, grades, syllabus, announcements, and quizzes."
                ),
            }
            # research_agent is intentionally omitted — it is not a directly-callable delegate; it
            # is the worker pool behind deep_research_agent's research_discover/parallel_research
            # tools (deep_research_agent itself appears in the roster below like any other agent).
            _roster = "\n".join([
                f"- **{name}**: {_AGENT_DESCRIPTIONS.get(name, 'Specialist agent.')}"
                for name in sub_agents_dict.keys() if name != "research_agent"
            ])
            orchestration_logic += (
                f"\n\n### 🤖 ACTIVE SPECIALIST TEAM\n"
                f"Only the following specialists are available right now. "
                f"Do NOT attempt to delegate to any agent not on this list:\n{_roster}"
            )


        # 3. Inject Skill Catalog (dynamic, changes as skills are added/removed)
        # supervisor_skill instructions are already in cloud_persona — only inject the catalog listing.
        # Uses on-demand disclosure based on user query to save ~1,400 tokens per turn when no skills match.
        # Telegram pocket remote skips the desktop skill catalog / MCP roster noise.
        if skill_loader and skill_catalog_names and not is_fixed_lane:
            try:
                # Extract user query from messages to find relevant skills
                user_query = ""
                for m in reversed(messages):
                    if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human":
                        user_query = m.content if isinstance(m.content, str) else str(m.content)
                        break

                skill_catalog = skill_loader.build_catalog_prompt(query=user_query)
                if skill_catalog:
                    orchestration_logic += f"\n\n{skill_catalog}"
            except Exception as _skill_err:
                logger.debug(f"[ SUPERVISOR ] Skill catalog prompt unavailable: {_skill_err}")

        if not is_fixed_lane:
            orchestration_logic += build_capability_directory(tool_registry, bound_tool_names)

        # 3b. Live MCP connectors — always inject so the model knows what call_mcp_tool
        # can reach (persona docs alone never list runtime connection state).
        if not is_fixed_lane:
            try:
                orchestration_logic += build_active_mcp_roster(tool_registry=tool_registry)
            except Exception as _mcp_roster_err:
                logger.debug(f"[ SUPERVISOR ] MCP roster unavailable: {_mcp_roster_err}")
        
        # 4. Active skill stub (full SOP is in the load_skill ToolMessage;
        #    specialist-owned skills also get the SOP on handoff)
        loaded_skill_content = state.get("loaded_skill")
        loaded_skill_name = state.get("loaded_skill_name")
        if loaded_skill_name:
            entry = skill_loader.get_entry(loaded_skill_name) if skill_loader else None
            subagent = entry.subagent if entry else None
            requires_str = (entry.requires or "") if entry else ""
            requires_mcp = "mcp" in requires_str.lower() or "call_mcp_tool" in requires_str.lower()
            orchestration_logic += format_active_skill_stub(loaded_skill_name, subagent, requires_mcp=requires_mcp)
        
        # 5. Add Dynamic Turn Data
        # Per-turn-VOLATILE content (plan goal, plan progress, current step, offline
        # tools, active skill SOP) is accumulated separately from the static system prompt and injected
        # into the trailing user turn below. Keeping it out of the system prefix
        # preserves the local model's KV cache across turns; putting it last also
        # gives it recency, which strengthens step-adherence (context-anchoring).
        turn_context = ""

        # Auto-inject active skill SOP for bundled or activated skills so the supervisor
        # has full domain playbooks immediately in context without needing an extra tool-call turn.
        if loaded_skill_content and loaded_skill_name:
            entry = skill_loader.get_entry(loaded_skill_name) if skill_loader else None
            subagent = entry.subagent if entry else None
            if not subagent:
                turn_context += (
                    f"\n\n### 📖 ACTIVE DOMAIN SKILL GUIDE: {loaded_skill_name}\n"
                    f"{loaded_skill_content.strip()}\n"
                )

        # Re-inject the ORIGINAL user request as the north star every round. On a long multi-round
        # plan the original ask is buried far up the message history (the latest HumanMessage is the
        # approval "proceed"), so without this the model drifts to serving only the terse current
        # step and forgets the overall objective and its nuances. Keep it adjacent to the plan.
        plan_goal = state.get("plan_goal")
        if plan and plan_goal:
            turn_context += (
                f"\n\n### 🎯 OVERALL OBJECTIVE (the user's original request — everything below serves THIS)\n"
                f"{str(plan_goal).strip()}\n"
                f"Stay anchored to this goal across every step; do not narrow to the current step so "
                f"much that you drop the user's stated constraints, deliverables, or level of depth.\n"
            )

        # Runway warning. The turn ceiling used to fire silently mid-work, which reads
        # to a user as "the model gave up" — and gives the model no chance to close out
        # cleanly. Warn while there is still runway left so it can choose what to
        # deliver, and say exactly how much is left rather than "you are running long".
        # This applies on EVERY lane, plan or not (the per-step budget only exists
        # when a plan is active, so without this a plain agent turn has no warning).
        turns_used = iteration
        turns_soft = int(limits.get("supervisor_turns_soft"))
        turns_max = int(limits.get("supervisor_turns_max"))
        if turns_used >= turns_soft:
            remaining = max(0, turns_max - turns_used)
            turn_context += (
                f"\n\n### ⏳ RUNWAY: {remaining} tool-turn(s) left in this turn "
                f"({turns_used}/{turns_max} used)\n"
                f"You are past the point where this should have taken. Land the plane:\n"
                f"- Prefer DELIVERING a good partial result now over starting anything new.\n"
                f"- If the deliverable mostly exists, finish and return it this turn.\n"
                f"- If you are stuck, say plainly what you got, what blocked you, and stop "
                f"— do not keep retrying.\n"
                f"- Do NOT start a fresh research pass, a new plan, or a new delegate.\n"
            )

        # Reflect-then-repair: on a second consecutive failure of the same tool,
        # make the model state its diagnosis before the next call. The measured
        # gain comes from the model producing the diagnosis, which the harness-
        # authored recovery text below cannot substitute for. See reflect.py.
        reflection = reflect.build_reflection_directive(messages)
        if reflection:
            turn_context += reflection

        # Consumed once: stall / fail_step recovery — try a different approach, do not replan.
        failure_context = state.get("failure_context")
        consume_failure_context = bool(failure_context)
        stall_recovery_set = False
        if failure_context:
            turn_context += (
                f"\n\n### ⚠️ STEP RECOVERY — different approach required\n"
                f"{failure_context}\n"
                f"Do NOT call create_plan. Do NOT re-gather from scratch. Change tools/strategy and finish this step.\n"
            )

        if plan:
            total_steps = len(plan)
            completed_count = sum(1 for s in plan if s.get("status") == "completed")
            plan_lines = []
            for s in plan:
                tool_hint = f" [tools: {', '.join(s['tools'])}]" if s.get("tools") else ""
                summary_hint = f" -> Result: {s['summary']}" if s.get("summary") else ""
                plan_lines.append(f"- [{s.get('status', 'pending').upper()}] {s.get('id')}: {s.get('description')}{tool_hint}{summary_hint}")
            turn_context += f"\n\n### 📋 EXECUTION PLAN ({completed_count}/{total_steps} complete)\n" + "\n".join(plan_lines) + "\n"

            active_step = next((s for s in plan if s.get("status") in ["pending", "failed", "running"]), None)
            if active_step:
                step_num = next(
                    (i + 1 for i, s in enumerate(plan) if s.get("id") == active_step.get("id")), completed_count + 1
                )
                turn_context += (
                    f"\n**▶ You are on Step {step_num} of {total_steps}: '{active_step.get('description')}'**\n"
                    f"\n⚡ MANDATORY `complete_step`: When this step's work is completed or its deliverable exists, "
                    f"you MUST call `complete_step(step_id='{active_step.get('id')}', summary='...')`. "
                    f"NEVER end your turn with conversational text alone while this step is open — emit `complete_step` "
                    f"immediately so the plan formally advances.\n"
                    f"If this step cannot be completed, call `fail_step(step_id='{active_step.get('id')}', reason='...')` "
                    f"(supervisor will retry the same step with a different approach — this does NOT wipe the plan).\n"
                    f"If you need user input before continuing, call `pause_for_input(message='...')`.\n"
                    # Anti-deferral: small models tend to end an execution turn by NARRATING intent
                    # ("I have finished round 1, I will now do the deep-dive...") and then stop —
                    # yielding to a user who is not there. On an autonomous plan run nobody replies,
                    # so the plan silently dies with 0 steps complete. Forbid the status-update-and-yield.
                    f"\n🚫 DO NOT end your turn with a progress update or a promise to continue "
                    f"(\"I will now…\", \"next I'll…\", \"proceeding with the deep-dive now\"). You are "
                    f"executing autonomously — there is no one to hand back to. Every turn MUST end in a "
                    f"tool call that advances the work: delegate to a specialist, call a tool, or call "
                    f"`complete_step`/`fail_step`/`pause_for_input`. If the deliverable is already created, "
                    f"call `complete_step` NOW. Do not propose a new plan or loop on verification.\n"
                    # Research delegation (#510): ANY research (one item or many) goes through
                    # deep_research_agent, which owns discovery + parallel depth research +
                    # synthesis internally — the supervisor never touches web_search/parallel_research
                    # itself for a research step.
                    f"\n⚡ RESEARCH: when this step needs web research — one item or several (e.g. "
                    f"caterers/products/competitors/facts) — call `delegate_to_deep_research_agent` "
                    f"ONCE with the full research goal (what to find, how many items, the fields/format "
                    f"you need). It handles discovery and parallel depth research internally and returns "
                    f"one synthesized report. Do NOT call web_search or research items one at a time "
                    f"yourself.\n"
                )
                stall_turns = int(active_step.get("_turn_count") or 0)
                if stall_turns >= _step_turn_soft() and not failure_context:
                    turn_context += (
                        f"\n⏱️ This step has used {stall_turns} supervisor tool-turns without "
                        f"`complete_step`. You may be looping. Switch strategy (different tool/"
                        f"delegate/path) rather than repeating the same failing call.\n"
                    )
                # This step was planned to use deep_research_agent — make it MANDATORY and unmistakable.
                if step_requires_parallel:
                    orchestration_logic += (
                        f"\n⛔ THIS STEP REQUIRES RESEARCH. Call `delegate_to_deep_research_agent` NOW, "
                        f"exactly once, with the full research goal (what to find for each item, how "
                        f"many items, and the fields/format to return). It fans out to parallel workers "
                        f"internally — do not research the items yourself.\n"
                    )

                # Resolve step resources against registry tools AND skill catalog
                step_tool_refs = active_step.get("tools") or []
                registry_names = {
                    t.name for t in current_turn_tools if t.name not in _STEP_MGMT_TOOLS
                }
                if step_tool_refs:
                    direct_usable, skill_hints, offline_refs = classify_step_resources(
                        step_tool_refs,
                        registry_names,
                        skill_catalog_names,
                    )
                    if direct_usable:
                        turn_context += (
                            f"💡 USE THESE TOOLS for this step: {', '.join(direct_usable)}\n"
                        )
                    if skill_hints:
                        skill_lines = [
                            f"load_skill('{canonical}')"
                            for _, canonical in skill_hints
                        ]
                        turn_context += (
                            f"📖 SKILLS for this step (available — call `load_skill`): "
                            f"{', '.join(skill_lines)}\n"
                        )
                    if offline_refs:
                        delegate_hints = delegate_hints_for_step_tools(step_tool_refs)
                        if delegate_hints:
                            turn_context += (
                                f"💡 DELEGATE for this step: {', '.join(delegate_hints)}\n"
                            )
                        else:
                            turn_context += (
                                f"⚠️ Step resources not found (not a tool or skill): "
                                f"{', '.join(offline_refs)}\n"
                            )
                else:
                    hints: list[str] = []
                    if registry_names:
                        hints.append(
                            f"Tools: {', '.join(sorted(registry_names))}"
                        )
                    if skill_catalog_names:
                        hints.append(
                            f"Skills (via load_skill): {', '.join(sorted(skill_catalog_names))}"
                        )
                    if hints:
                        turn_context += (
                            f"💡 Available for this step: {' | '.join(hints)}. "
                            f"Use whichever fits.\n"
                        )
            else:
                turn_context += (
                    f"\n\n### 🏁 ALL PLAN STEPS COMPLETED — DELIVER FINAL RESPONSE\n"
                    f"All {total_steps} execution steps are complete. Provide your final response and deliverable summary to the user now.\n"
                    f"GROUNDING REQUIREMENTS (CRITICAL):\n"
                    f"1. Base your summary ONLY on the real-world data retrieved by tools during execution (the actual assignment titles, calendar events, email subjects, etc.).\n"
                    f"2. NEVER invent, hallucinate, or substitute placeholder items (e.g. 'Math HW', 'Science Project', 'Team Meeting', 'Client Call', 'Pay Rent', 'Buy Groceries').\n"
                    f"3. Summarize exactly what was found and cite the deliverable artifact.\n"
                )

        if offline_tools:
            offline_list = ", ".join(offline_tools)
            turn_context += f"\n\n⚠️ OFFLINE TOOLS: The following tools were requested but are currently OFFLINE or UNREACHABLE: {offline_list}. Do NOT attempt to use them. Explain this limitation to the user if relevant."

        # 6. Add Request Metadata (HUD Mode)
        metadata = state.get("metadata")
        if metadata and metadata.get("origin") == "hud":
            orchestration_logic += (
                "\n\n### 🟢 HUD MODE ACTIVE\n"
                "The user is interacting with you via a minimal Command Bar (HUD).\n"
                "- Keep your responses CONCISE and compact.\n"
                "- Avoid long paragraphs; use bullet points if necessary.\n"
                "- If the task requires deep research or long-form writing, tell the user 'Working on it in the main UI' and then perform the work."
            )
        
        # Assemble final prompt. The system prompt is kept byte-stable across turns
        # (persona + STATIC orchestration only) so the local model can reuse its KV
        # cache for the whole conversation prefix instead of re-prefilling it every
        # turn. All per-turn-volatile content — plan/step status, offline tools, and
        # personal knowledge (graph + activity RAG) — rides in the trailing user turn
        # instead (the uncached tail). See _inject_turn_context_into_last_turn.
        prompt_content = f"{cloud_persona}\n\n{orchestration_logic}"
        knowledge_boost = state.get("knowledge_boost", "") or ""
        scout_context = render_evidence(state.get("scout_evidence")) if plan else ""
        trailing_context = f"{turn_context}{knowledge_boost}{scout_context}"
        # Reclaim context from repeated identical failures BEFORE the turn context is
        # appended, so a long retry streak can't crowd out the plan and the goal.
        # Both transforms copy; graph history stays untouched.
        turn_messages = _inject_turn_context_into_last_turn(
            _collapse_repeated_failures(messages), trailing_context
        )

        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=prompt_content),
            MessagesPlaceholder(variable_name="messages"),
        ])

        chain = prompt | supervisor_model
        
        # --- PROPAGATE PERSONA & SKILLS TO TOOLS ---
        # We store context in the config so that handoff tools 
        # can pass it down to sub-agents.
        if "configurable" not in config:
            config["configurable"] = {}
        # Sub-agents still receive the personal knowledge (they don't share the
        # supervisor's KV cache), so fold it back into the persona passed downstream.
        config["configurable"]["system_instruction"] = (
            f"{cloud_persona}{knowledge_boost}" if knowledge_boost else cloud_persona
        )
        config["configurable"]["scout_evidence"] = state.get("scout_evidence") if plan else []
        config["configurable"]["loaded_skill"] = loaded_skill_content
        config["configurable"]["loaded_skill_name"] = loaded_skill_name
        
        # Propagate session metadata into the LangSmith run for this LLM call
        if "metadata" not in config:
            config["metadata"] = {}
        config["metadata"].update({
            "session_id": str(state.get("session_id") or state.get("turn_id") or ""),
            "iteration": str(iteration),
        })
        config.setdefault("tags", [])
        if "supervisor-llm" not in config["tags"]:
            config["tags"] = list(config.get("tags", [])) + ["supervisor-llm"]

        # Invoke supervisor model (turn_messages carries per-turn RAG in the tail;
        # `messages` in graph state is left untouched so history stays cache-stable).
        response = await chain.ainvoke({"messages": turn_messages}, config=config)
        
        # Mark the active step as running when the model calls a tool (but hasn't completed it yet)
        if plan and response.tool_calls:
            active_step_id = state.get("plan_step")
            active_step = next(
                (s for s in plan if s.get("id") == active_step_id and s.get("status") in ["pending", "running"]),
                next((s for s in plan if s.get("status") in ["pending", "running"]), None)
            )
            if active_step:
                # Track supervisor tool-turns per step (not wall-clock) as a stall safety net
                turn_cnt = active_step.get("_turn_count", 0) + 1
                active_step["_turn_count"] = turn_cnt
                active_step["status"] = "running"

                if turn_cnt >= _step_turn_hard():
                    # Force a different approach on the SAME step — do not fail/skip/replan.
                    recovery = _build_step_recovery_feedback(
                        active_step,
                        messages,
                        reason=f"{turn_cnt} supervisor tool-turns without complete_step",
                    )
                    state["failure_context"] = recovery
                    stall_recovery_set = True
                    active_step["_turn_count"] = 0
                    state["plan_step"] = active_step.get("id") or state.get("plan_step") or ""
                    logger.warning(
                        "[ SUPERVISOR ] Step '%s' stall ceiling (%d) — recovery via different "
                        "approach (no scout/replan).",
                        active_step.get("id"),
                        _step_turn_hard(),
                    )
                    # Drop the looping tool calls so the next supervisor pass rethinks.
                    response = AIMessage(
                        content=(
                            (response.content or "")
                            + f"\n\n[Step '{active_step.get('id')}' stall ceiling — "
                            f"change approach; do not repeat the same failing tools.]"
                        ),
                        id=getattr(response, "id", None),
                        additional_kwargs=getattr(response, "additional_kwargs", None) or {},
                    )
                elif turn_cnt == _step_turn_soft():
                    logger.info(
                        "[ SUPERVISOR ] Step '%s' soft stall nudge at %d tool-turns.",
                        active_step.get("id"),
                        turn_cnt,
                    )

                PlanService.emit_snapshot(state, plan)

        # Clear only the recovery text we already showed this turn. Keep a freshly
        # set stall-ceiling recovery for the next supervisor pass.
        if stall_recovery_set:
            out_failure = state.get("failure_context")
        elif consume_failure_context:
            out_failure = None
        else:
            out_failure = state.get("failure_context")

        return {
            "messages": [response],
            "iteration_count": iteration,
            "active_plan": plan,
            "plan_step": state.get("plan_step") or (plan[0]["id"] if plan else ""),
            "failure_context": out_failure,
        }
        
    return call_supervisor
