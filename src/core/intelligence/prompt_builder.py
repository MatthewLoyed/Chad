"""
System Prompt Builder - Centralized Prompt Construction
Standardizes how system instructions are built across Voice and Text modes.
"""

from datetime import datetime
from typing import Optional, Any, List
import logging
import re
from pathlib import Path


class SystemPromptBuilder:
    """
    Constructs the final system instruction.
    Technique: Prefers modular Markdown files from src/core/.agent/ over hardcoded strings.
    """

    @staticmethod
    def current_date_block() -> str:
        """The model has no innate sense of "today" — a local model in particular defaults
        to a date near its training cutoff (observed live: Gemma reasoning "June 2024" while
        the real date was July 2026, which threw off a wedding-budget calculation by ~3x on
        the available time/income). Cloud providers (Claude, Gemini, ChatGPT) solve this by
        injecting the date into the system prompt on every turn rather than relying on the
        model choosing to call a datetime tool — `get_datetime` stays available for
        timezone-specific or mid-conversation re-checks, but this is the primary mechanism.

        Placed in the system prompt (not the volatile per-turn RAG boost) because it changes
        once per CALENDAR DAY, not per query — the KV-cache cost of that is one miss/day,
        negligible next to the per-query volatility this architecture already isolates
        elsewhere (see graphrag_node.py's knowledge_boost).
        """
        now = datetime.now()
        return (
            "### CURRENT DATE\n"
            f"Today is {now.strftime('%A, %B %d, %Y')} ({now.strftime('%Y-%m-%d')}). "
            "Use this for any date-math (deadlines, \"how long until X\", scheduling) instead "
            "of assuming a date — do not rely on your training data for what \"today\" is."
        )

    # Scopes that talk to the user and therefore need to know who the user is.
    # Worker scopes (planner/validator/scout/subagents) already have the rest of the
    # `always` persona stripped; conversational is held to a token cap (#550).
    _CORE_PROFILE_SCOPES = {"supervisor", "heartbeat", "voice", "contact", "telegram", "conversational"}

    @staticmethod
    def core_profile_block() -> str:
        """The always-on identity block — who the user is, unconditionally.

        Everything else personal reaches the model through query-matched retrieval:
        similarity threshold, item cap, char budget. That is right for the long tail
        and wrong for the handful of facts that are load-bearing on almost any turn
        (who you live with, what you do, what's coming up). When retrieval ranked
        those out — and it did, reproducibly — Chad would ask the user to re-explain
        their own life. This block is not retrieved and cannot be ranked out.

        Lives in the SYSTEM prompt, not the per-turn knowledge boost, for the same
        reason as current_date_block(): it is stable across turns, so it costs one
        prefill and is KV-cached thereafter. Source of truth is the ## _core_profile
        section of config/personal_knowledge.md.
        """
        try:
            from src.core.intelligence.graph_rag_client import GraphRAGClient

            profile = GraphRAGClient.read_core_profile_sync()
        except Exception as e:
            logging.debug(f"[ PROMPT ] Core profile unavailable: {e}")
            return ""
        if not profile:
            return ""
        return (
            "### CORE PROFILE\n"
            "Durable facts about the user. These are always true — treat them as known, "
            "do not ask the user to confirm them, and do not narrate that you looked "
            "them up.\n"
            f"{profile}"
        )

    @staticmethod
    def upcoming_schedule_block(lookahead_hours: int = 4) -> str:
        """Surfaces upcoming Google Calendar events for proactive awareness."""
        try:
            from src.shared.config_manager import ConfigManager
            if not ConfigManager.get("calendar.proactive_awareness", True):
                return ""
            from src.tools.workspace.calendar import get_upcoming_schedule_context
            return get_upcoming_schedule_context(lookahead_hours=lookahead_hours)
        except Exception as e:
            logging.debug(f"[ PROMPT ] Upcoming schedule context unavailable: {e}")
            return ""

    @staticmethod
    def user_behavior_block() -> str:
        """User's standing directives about how Chad should behave.

        These used to live in the graph as ordinary fact nodes, where they were
        semantically bland enough to weakly match almost any query — "prefer recent
        items when using email/Drive memory" surfaced while debugging a traceback.
        They are doctrine, not recall: always on, never retrieved.
        """
        try:
            from src.core.intelligence.graph_rag_client import GraphRAGClient

            behavior = GraphRAGClient.read_behavior_sync()
        except Exception as e:
            logging.debug(f"[ PROMPT ] User behavior directives unavailable: {e}")
            return ""
        if not behavior:
            return ""
        return f"### USER PREFERENCES\n{behavior}"

    @staticmethod
    def _read_agent_files(initiator: str = "user", platform: str = "pc", is_conversational: bool = False, uses_browser: bool = False, scope: str = "supervisor") -> str:
        """Reads persona and protocol records strictly from the PromptRegistry (Master Source) filtering by scope."""
        from src.infrastructure.repositories.prompt_registry import PromptRegistry
        registry = PromptRegistry.get_instance()
        
        # Determine allowed scopes based on target scope
        if scope not in ["conversational", "heartbeat", "supervisor", "planner", "validator", "voice", "scout", "contact", "telegram"]:
            # Subagent scopes: require the specific subagent prompt, plus always and subagent_base
            registry.get_prompt(scope)
            allowed_scopes = [scope, "always", "subagent_base"]
            logging.info(f"[ PROMPT ] Building subagent persona for scope: '{scope}'")
        else:
            # Planner, Validator, Supervisor, Scout, Heartbeat, Conversational, Voice, Contact, Telegram
            # Only force-load fragments that exist as dedicated files (contact/voice use node
            # instruction addenda, not a required persona id).
            if scope in ["planner", "validator", "scout", "telegram"]:
                registry.get_prompt(scope)
            allowed_scopes = [scope, "always"]
            logging.info(f"[ PROMPT ] Building persona for scope: '{scope}'")
            
        # Retrieve all prompts that match the target scopes
        matched_prompts = registry.get_prompts_by_scopes(allowed_scopes)

        # Scout has one narrow role; unrelated always-scoped behavior fragments
        # must not turn reconnaissance into supervisor execution.
        if scope == "scout":
            matched_prompts = {pid: content for pid, content in matched_prompts.items() if pid == "scout"}

        # Worker scopes (planner, validator, scout, and every subagent) never speak to the
        # user and never do personal-fact CRUD, yet the `always`-scoped persona stack
        # (identity/soul/user/memory_policy/learned-lessons) lands in them anyway — ~1k tokens
        # of voice/memory doctrine that only dilutes a small model emitting a JSON plan, a
        # PASS/FAIL verdict, or a terse report to the Supervisor. Strip those fragments for
        # worker scopes. The functional WORKSPACE root (`agent`), the scope's own role, and
        # `subagent_base` are kept. User-facing scopes are untouched. This is deploy-gated:
        # it changes nothing in Supabase, so the live Brain is unaffected until this ships.
        USER_FACING_SCOPES = {"supervisor", "conversational", "heartbeat", "voice", "contact", "telegram"}
        WORKER_EXCLUDED_PERSONA = {"identity", "soul", "user", "memory_policy", "tools"}
        # Workspace root is only useful for agents that touch the local project tree.
        FILESYSTEM_WORKER_SCOPES = {
            "supervisor", "code_agent", "system_agent", "traversal_agent",
        }
        if scope not in USER_FACING_SCOPES:
            matched_prompts = {
                pid: content
                for pid, content in matched_prompts.items()
                if pid not in WORKER_EXCLUDED_PERSONA
            }
            if scope not in FILESYSTEM_WORKER_SCOPES:
                matched_prompts = {
                    pid: content
                    for pid, content in matched_prompts.items()
                    if pid != "agent"
                }

        # conversational/heartbeat never create files (rich_output's inline-vs-file guidance is
        # dead weight there — heartbeat's own Output Rules already own its formatting), and
        # conversational binds no tools at all, so the auto-authored "tools" learned-lessons
        # fragment is unreachable content. Drop both to keep these user-facing scopes under
        # their token caps (#550).
        if scope == "conversational":
            matched_prompts = {
                pid: content for pid, content in matched_prompts.items()
                if pid not in {"rich_output", "tools"}
            }
        elif scope in ("heartbeat", "telegram", "contact", "voice"):
            matched_prompts = {
                pid: content for pid, content in matched_prompts.items()
                if pid != "rich_output"
            }

        # Option A: Strip disabled-agent persona rows from the supervisor prompt so Chad
        # has zero awareness of deactivated specialists.
        if scope == "supervisor":
            from src.shared.config_manager import ConfigManager
            from src.core.orchestration.agents.factory import AGENT_CONFIGS
            disabled_agents = set(ConfigManager.get("disabled_subagents") or [])
            agent_scoped_ids = set(AGENT_CONFIGS.keys())
            if disabled_agents:
                matched_prompts = {
                    pid: content
                    for pid, content in matched_prompts.items()
                    if pid not in (disabled_agents & agent_scoped_ids)
                }
                logging.info(
                    f"[ PROMPT ] Filtered out {len(disabled_agents & agent_scoped_ids & set(matched_prompts.keys() | disabled_agents))} "
                    f"disabled-agent prompt(s): {disabled_agents & agent_scoped_ids}"
                )

        # Maintain a deterministic sorting order for persona components.
        # Active agent IDs are derived dynamically so disabled agents are never ordered in.
        from src.core.orchestration.agents.factory import AGENT_CONFIGS as _AGENT_CONFIGS
        from src.shared.config_manager import ConfigManager as _CM
        _disabled = set(_CM.get("disabled_subagents") or [])
        _active_agent_ids = [a for a in _AGENT_CONFIGS.keys() if a not in _disabled]

        # Defense-in-depth: never let an assembled prompt MANDATE routing to a DISABLED
        # subagent. The dynamic {active_agents} list is correct, but static prompt text
        # (e.g. a Supabase "MANDATORY SUBAGENT ROUTING" block) can still name disabled
        # `delegate_to_*` tools — a contradiction the model cannot satisfy (it's ordered to
        # delegate to an agent that doesn't exist). Drop any line referencing one.
        _disabled_delegates = {f"delegate_to_{a}" for a in _disabled}

        def _strip_disabled_delegate_lines(text: str) -> str:
            if not _disabled_delegates:
                return text
            return "\n".join(
                ln for ln in text.splitlines()
                if not any(d in ln for d in _disabled_delegates)
            )
        ordering = [
            "identity", "soul", "user", "memory_policy", "rich_output", "agent", "tool_calling",
            "mcp_instructions", "heartbeat", "heartbeat_protocol", "planner", "validator",
            "telegram", "contact", "voice", "conversational",
            *_active_agent_ids,
            "subagent_base"
        ]
        
        # Sort keys according to defined order, and append any remaining matching ones at the end
        sorted_pids = [pid for pid in ordering if pid in matched_prompts]
        for pid in matched_prompts:
            if pid not in sorted_pids:
                sorted_pids.append(pid)

        blocks = []
        for pid in sorted_pids:
            content = matched_prompts[pid]
            if content and content.strip():
                # Interpolate project root if placeholder is present
                if "{project_root}" in content:
                    try:
                        from src.shared.paths import get_project_root
                        project_root = str(get_project_root()).replace("\\", "/")
                        content = content.replace("{project_root}", project_root)
                    except Exception as e:
                        logging.warning(f"[ PROMPT ] Failed to resolve project_root: {e}")
                # Interpolate mcp root if placeholder is present
                if "{mcp_root}" in content:
                    try:
                        from src.shared.config_manager import ConfigManager
                        from src.shared.paths import resolve_path
                        mcp_root_raw = ConfigManager.get("mcp.schema_root", "mcp")
                        mcp_root_path = resolve_path(mcp_root_raw).replace("\\", "/")
                        content = content.replace("{mcp_root}", mcp_root_path)
                    except Exception as e:
                        logging.warning(f"[ PROMPT ] Failed to resolve mcp_root: {e}")
                # Interpolate chad email if placeholder is present
                if "{chad_email}" in content:
                    try:
                        import os
                        chad_email = os.getenv("CHAD_EMAIL", "")
                        content = content.replace("{chad_email}", chad_email)
                    except Exception as e:
                        logging.warning(f"[ PROMPT ] Failed to resolve chad_email: {e}")
                # Interpolate active_agents list if placeholder is present.
                # Resolves to a bullet list of currently-enabled specialist agents so
                # routing prompts never mention disabled agents.
                if "{active_agents}" in content:
                    try:
                        from src.core.orchestration.agents.factory import AGENT_CONFIGS as _AC
                        from src.shared.config_manager import ConfigManager as _CMi
                        from src.infrastructure.tools.web_search_routing import BROWSER_AGENT_SUPERVISOR_ROSTER
                        _AGENT_DESCRIPTIONS = {
                            "ui_automation_agent": "Clicks/types in local Windows apps and executes UI automation flows.",
                            "browser_agent": BROWSER_AGENT_SUPERVISOR_ROSTER,
                            "research_agent": (
                                "Deep multi-item web research with batch search and synthesis. "
                                "Quick single-topic lookups → web_search instead."
                            ),
                            "code_agent": (
                                "Light in-turn coding and UI artifacts (one-shot HTML, small scripts). "
                                "Deep multi-file work → Chad Code."
                            ),
                            "system_agent": "Manages local files, checks system stats, and adjusts OS settings.",
                            "traversal_agent": (
                                "Open-ended Drive/filesystem explore; known id/path reads stay on "
                                "the supervisor."
                            ),
                        }
                        _dis = set(_CMi.get("disabled_subagents") or [])
                        _active = [n for n in _AC.keys() if n not in _dis]
                        if scope == "scout":
                            if not _CMi.get("planner.scout_allow_delegates", False):
                                _active = []
                            else:
                                allow = _CMi.get("planner.scout_delegate_allowlist") or [
                                    "traversal_agent",
                                    "research_agent",
                                ]
                                _active = [n for n in _active if n in allow]
                        if _active:
                            active_agents_str = "\n".join(
                                f"- **{n}**: {_AGENT_DESCRIPTIONS.get(n, 'Specialist agent.')}"
                                for n in _active
                            )
                        else:
                            active_agents_str = "*(No specialist agents are currently active.)*"
                        content = content.replace("{active_agents}", active_agents_str)
                    except Exception as e:
                        logging.warning(f"[ PROMPT ] Failed to resolve active_agents: {e}")
                # Remove any mandate to delegate to a disabled agent (prevents the
                # "ordered to use an agent that doesn't exist" contradiction).
                content = _strip_disabled_delegate_lines(content)
                if not content.strip():
                    continue
                # Fragment markers for LLM context (local files — never "Cloud Master")
                blocks.append(f"### {pid}\n{content.strip()}")

        
        return "\n\n".join(blocks)

    @staticmethod
    def build(
        base_instruction: str,
        memory_engine: Optional[Any] = None,
        initiator: str = "user",
        platform: str = "pc",
        user_input: Optional[str] = None,
        is_conversational: bool = False,
        uses_browser: bool = False,
        history: Optional[List[Any]] = None,
        scope: Optional[str] = None,
        domain: Optional[str] = None,
        domain_persona: Optional[str] = None,
    ) -> str:
        """Assemble the system prompt.

        `domain` / `domain_persona` carry the active domain agent (Financial, Fitness, ...).
        The persona is injected directly here rather than through PromptRegistry scopes:
        a user-created id is not a known scope, and an overlay fragment with no baseline
        resolves to scope "always" (persona_store.py), which would spray every domain's
        persona into every prompt — the exact opposite of the isolation this provides.
        """
        # Determine scope from context parameters if not explicitly provided
        if not scope:
            if is_conversational:
                scope = "conversational"
            elif initiator == "system":
                scope = "heartbeat"
            else:
                scope = "supervisor"

        voice_mode = scope == "voice"
        contact_mode = scope == "contact"

        # Contact must NOT load the conversational fragment — that fragment says
        # "you cannot run tools / access email; say so plainly", which fights the
        # escalate_to_agent handoff. Reuse voice's lean always+role scopes instead.
        agent_context = SystemPromptBuilder._read_agent_files(
            initiator=initiator, 
            platform=platform,
            is_conversational=is_conversational,
            uses_browser=uses_browser,
            scope=("voice" if contact_mode else scope),
        )

        parts = []

        if agent_context:
            parts.append(agent_context)
        else:
            # 2. Fallback to Legacy Persona logic (if .agent files are missing)
            parts.append(base_instruction)

        # Constant for the whole thread, so it sits safely in the cacheable system prefix.
        # Never assemble this from per-turn data — that would invalidate the KV-cache prefix
        # on every turn, which is why retrieved memory goes in the trailing user turn instead.
        if domain_persona and domain_persona.strip():
            parts.append("### DOMAIN CONTEXT\n" + domain_persona.strip())

        parts.append(SystemPromptBuilder.current_date_block())

        if scope in SystemPromptBuilder._CORE_PROFILE_SCOPES:
            for block in (
                SystemPromptBuilder.core_profile_block(),
                SystemPromptBuilder.user_behavior_block(),
            ):
                if block:
                    parts.append(block)

        # Voice, Telegram, and Contact scopes get direct schedule awareness
        # (Supervisor receives it in trailing turn context via knowledge_boost to preserve system prefix KV-cache)
        if scope in ("voice", "telegram", "contact"):
            sched_block = SystemPromptBuilder.upcoming_schedule_block()
            if sched_block:
                parts.append(sched_block)

        # Inject dynamic Memory (Stateful context)
        if memory_engine:
            try:
                if voice_mode:
                    from src.shared.config_manager import ConfigManager
                    max_results = int(ConfigManager.get("voice_mode.memory_max_results", 2))
                    memory_block = memory_engine.get_unified_context(
                        query=user_input, history=history, domain=domain
                    )
                    if memory_block:
                        lines = [
                            ln for ln in memory_block.splitlines()
                            if ln.strip() and not ln.strip().startswith("###")
                        ][:max_results]
                        if lines:
                            parts.append(
                                "### VOICE CONTEXT (brief)\n" + "\n".join(lines[:max_results])
                            )
                elif contact_mode:
                    from src.remote.telegram.contact_context import build_lean_memory_lines
                    lean = build_lean_memory_lines(
                        memory_engine, user_input, history, max_results=5, domain=domain
                    )
                    if lean:
                        parts.append(lean)
                else:
                    if scope == "conversational":
                        pass  # fast lane — skip memory injection
                    else:
                        memory_block = memory_engine.get_memory_block(
                            query=user_input, history=history, domain=domain
                        )
                        if memory_block:
                            parts.append(memory_block)
            except Exception:
                logging.warning("[ PROMPT ] Failed to get memory block from engine.")

        if voice_mode and not agent_context:
            parts.insert(
                0,
                "You are Chad on a hands-free voice command. Be brief, warm, and spoken-aloud friendly.",
            )
        if contact_mode and not agent_context:
            parts.insert(
                0,
                "You are Chad texting on Telegram. Be brief, warm, and personal.",
            )
        

        if not parts:
            # Final fallback: Standard Chad persona if everything else failed
            parts.append("You are CHAD. Use your tools to assist.")

        final_prompt = "\n\n".join(parts)
        return final_prompt
