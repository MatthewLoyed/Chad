from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger
from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.core.orchestration.graph.state import ChadState
from src.core.orchestration.context import domain_var
from src.infrastructure.repositories.domain_repository import get_domain_persona
from src.shared.domain_scope import normalize_domain

logger = setup_logger("TurnStateBuilder")


class TurnStateBuilder:
    """Resolves the system-prompt scope and assembles the LangGraph ``ChadState``
    for a turn kickoff.

    Both are mechanical (a scope if/elif chain and a ~30-key dict literal) but
    previously lived inline in ``TurnCoordinator._execute_turn_internal_wrapped``,
    where they padded out an already-overloaded method. Giving them a name here
    is the whole point: the coordinator now reads as "resolve scope, build
    state" instead of burying both in a wall of turn-execution code.
    """

    @staticmethod
    def build_system_instruction(
        *,
        category: str,
        turn_mode: str,
        initiator: Optional[str],
        platform: Optional[str],
        prompt: str,
        history: List[Dict[str, Any]],
        memory_engine: Optional[Any],
        session_id: Optional[str] = None,
    ) -> str:
        """Plan lane gets a bare memory block (scout/planner nodes add their own
        scoped role prompts); every other lane gets the full assembled prompt."""
        # Resolved once for BOTH lanes below. The plan lane bypasses SystemPromptBuilder.build
        # entirely, so it has to inject the domain persona and scope its memory itself —
        # forgetting that here is how a Financial plan turn ends up reading global memory.
        domain = normalize_domain(domain_var.get())
        domain_persona = get_domain_persona(domain).strip()

        if category == "planning" or turn_mode == "plan":
            # Plan turns (trip itineraries, project timelines, deadline-driven goals) are
            # exactly the ones most likely to need date-math, so the date block goes in here
            # too rather than only on the non-plan path below.
            parts = [SystemPromptBuilder.current_date_block()]
            if domain_persona:
                parts.append("### DOMAIN CONTEXT\n" + domain_persona)
            try:
                memory_block = memory_engine.get_memory_block(
                    query=prompt, history=history, domain=domain
                )
                if memory_block and memory_block.strip():
                    parts.append(memory_block.strip())
            except Exception as e:
                logger.warning(f"[ TURN ] Plan-lane memory block failed: {e}")
            return "\n\n".join(parts)

        is_telegram = (
            platform == "telegram"
            or category in ("contact", "telegram_agent")
            or bool(session_id and (":telegram:" in session_id or session_id.startswith("tg:")))
        )

        if category == "voice":
            scope = "voice"
        elif is_telegram:
            scope = "telegram"
        elif turn_mode == "chat":
            scope = "conversational"
        elif initiator == "system" or platform in ("system", "background"):
            scope = "heartbeat"
        else:
            scope = "supervisor"

        instruction = SystemPromptBuilder.build(
            base_instruction="",
            memory_engine=memory_engine,
            initiator=initiator or "user",
            platform=platform or "pc",
            user_input=prompt,
            is_conversational=(turn_mode == "chat" or is_telegram),
            history=history,
            scope=scope,
            domain=domain,
            domain_persona=domain_persona,
        )

        if is_telegram:
            from src.remote.telegram.contact_context import build_contact_peer_block

            peer = build_contact_peer_block(session_key=session_id)
            if peer:
                instruction = f"{instruction}\n\n{peer}" if instruction else peer

        return instruction

    @staticmethod
    def build(
        *,
        lc_messages: Any,
        turn_id: str,
        session_id: str,
        category: str,
        turn_mode: str,
        platform: Optional[str],
        is_simple: bool,
        sys_instr: str,
        tools: List[Any],
        max_tool_calls: int,
        thinking_level: str,
        bundled_skill_content: Optional[str],
        bundled_skill_name: Optional[str],
        telemetry: Any,
        metadata: Optional[Dict[str, Any]],
        resuming_thread: bool = False,
        knowledge_boost: str = "",
    ) -> ChadState:
        state: ChadState = {
            "messages": lc_messages,
            "turn_id": turn_id,
            "session_id": session_id,
            "category": category,
            "turn_mode": turn_mode,
            "platform": platform or "pc",
            "is_simple": is_simple,
            "active_plan": None,
            "plan_step": "",
            "failure_context": None,
            "validation_feedback": None,
            "paused_for_input": None,
            "pending_approval": None,
            "scout_evidence": [],
            "scout_coverage": None,
            "scout_episode_id": None,
            "gather_brief": None,
            "scout_decision": None,
            "scout_dispatch_count": 0,
            "scout_gather_request": None,
            "confirmed_tool_menu": None,
            "system_instruction": sys_instr,
            # Pre-fetched during tool selection so graphrag_injector can reuse it
            # instead of querying graph/activity/drive/session a second time.
            "knowledge_boost": knowledge_boost or "",
            "tools": tools,
            "total_tool_calls": 0,
            "tool_results": [],
            "max_tool_calls": 4 if category in ("voice", "contact") else (
                12 if category == "telegram_agent" else max_tool_calls
            ),
            "final_response": None,
            "temperature": 0.7,
            "thinking_level": thinking_level,
            "loaded_skill": bundled_skill_content,
            "loaded_skill_name": bundled_skill_name,
            "voice_escalate": None,
            "voice_escalate_reason": None,
            "voice_tool_rounds": 0,
            "chat_tool_rounds": 0,
            "on_token": telemetry.on_token,
            "on_thinking": telemetry.on_thinking,
            "on_usage": telemetry.on_usage,
            "metadata": metadata or {},
            "variables": {},
        }

        if resuming_thread:
            # LangGraph's checkpointer (keyed by thread_id) is the source of truth once a
            # thread already exists — turn_coordinator.py deliberately passes only the new
            # user message in that case, relying on the checkpointer for everything else. But
            # any key EXPLICITLY present in this dict overwrites its checkpointed channel value
            # on invoke(), so leaving "active_plan": None above would silently clobber an
            # in-progress plan on every follow-up turn (approvals, "keep going" nudges, etc.),
            # forcing route_entry to see no active plan and re-enter Scout -> Planner from
            # scratch mid-execution. Omit it here so LangGraph carries the real value forward.
            del state["active_plan"]
            # Evidence belongs to the checkpointed plan, including approval turns.
            # Planner explicitly resets it when a new planning episode starts.
            for key in ("scout_evidence", "scout_coverage", "scout_episode_id", "gather_brief",
                        "scout_decision", "scout_dispatch_count", "scout_gather_request", "confirmed_tool_menu"):
                state.pop(key, None)

        return state
