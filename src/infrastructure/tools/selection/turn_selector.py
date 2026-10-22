"""TurnToolSelector — per-turn tool selection plumbing.

Extracted from ToolRegistry (issue #247) to keep the registry a thin
coordinator. Owns the selection algorithm: core-tool injection, the LLM (9B)
selector vs. RAG fallback, hybrid shortlisting, bundle expansion, intent
seeding, and resolving the final schema set for a turn.

Operates against the registry's storage and sub-systems rather than owning
them, so the registry remains the single source of truth.
"""

import os
from typing import Any, Callable, Dict, List, Optional, Set

from src.shared.logger import setup_logger

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        return lambda func: func

logger = setup_logger("TurnToolSelector")



def pinned_tool_names() -> Optional[List[str]]:
    """Explicit tool menu from ``CHAD_PINNED_TOOLS``, or None for normal selection.

    Exists for eval lanes that measure something *other* than tool selection (#675).
    Running the 9B selector costs more than its own latency on a single-GPU box: the 9B
    doesn't fit alongside the 26B supervisor inside the VRAM budget, so every turn evicts
    the supervisor and then pays 15-20s to re-preload it. Over a 15-case lane that is
    roughly half the wall clock — and the repeated load/evict cycling is what the run kept
    dying inside of.

    Pinning the menu means the 9B never loads and the supervisor stays resident. Only sound
    for a lane whose bound tools are known up front (e.g. the memory-write lane, where
    ``manage_user_memory`` is always bound anyway, so fidelity is unaffected). Never set in
    production — unset is the normal path.

    Empty string is meaningful and distinct from unset: "bind core tools only".

    Module-level rather than a method because it reads process state and touches no
    instance state — and because TurnToolSelector is already over the cohesion limit on
    `dev` (412 lines against a 400 cap), so this feature should not push it further.
    """
    raw = os.getenv("CHAD_PINNED_TOOLS")
    if raw is None:
        return None
    return [name.strip() for name in raw.split(",") if name.strip()]


# Tools that are utility members of bundles (needed when a bundle is active)
# but MUST NOT themselves trigger expansion of all bundles they belong to.
# Without this filter, selecting generic 'write_file' or 'load_skill' causes
# transitive expansion of every bundle in the system (visual, music, maps).
NON_TRIGGERING_BUNDLE_TOOLS = {
    "load_skill",
    "write_file",
    "read_file",
    "resolve_path",
    "list_directory",
    "render_chart",
}


class TurnToolSelector:
    """Selects the tool schemas to bind for a single conversational turn."""

    def __init__(self, registry: Any):
        self._registry = registry

    # --- thin views onto registry state ------------------------------- #
    @property
    def _tools(self) -> Dict[str, Any]:
        return self._registry._tools

    @property
    def _categories(self) -> Dict[str, List[str]]:
        return self._registry._categories

    @property
    def _tool_sources(self) -> Dict[str, str]:
        return self._registry._tool_sources

    @property
    def _core_tool_names(self) -> Set[str]:
        return self._registry._core_tool_names

    @property
    def _selector(self) -> Any:
        return self._registry._selector

    @property
    def _llm_selector(self) -> Any:
        return self._registry._llm_selector

    @property
    def _bundle_groups(self) -> Dict[str, Set[str]]:
        return self._registry._bundle_groups

    @property
    def _bundle_skills(self) -> Dict[str, str]:
        return self._registry._bundle_skills

    def _extract_schema(self, name: str, tool: Any) -> Dict[str, Any]:
        return self._registry._schema_builder.extract_schema(name, tool)

    # --- helpers ------------------------------------------------------- #
    def _expand_bundles(self, names: List[str]) -> List[str]:
        """When any domain member of a bundle is selected, include the whole bundle."""
        expanded: Set[str] = set(names)
        changed = True
        while changed:
            changed = False
            for member in list(expanded):
                if member in NON_TRIGGERING_BUNDLE_TOOLS:
                    continue
                for group in self._bundle_groups.values():
                    if member in group:
                        before = len(expanded)
                        expanded |= group
                        if len(expanded) > before:
                            changed = True
        # Preserve original order where possible, append bundle extras at end
        ordered = [n for n in names if n in expanded]
        for n in expanded:
            if n not in ordered:
                ordered.append(n)
        return ordered

    @staticmethod
    def _is_explicit_canva_query(query: str) -> bool:
        q = (query or "").lower()
        import re
        return bool(re.search(r"\bcanva\b", q)) and not any(
            token in q for token in (
                "canvas", "assignment", "assignments", "coursework",
                "syllabus", "homework", "course", "lecture", "module",
                "grade", "quiz", "quizzes", "exam", "class", "classes",
            )
        )

    def _is_canvas_lms_tool(self, name: str) -> bool:
        if name.startswith("get_canvas_") or name.startswith("canvas_"):
            return True
        if name in self._categories.get("canvas", []):
            return True
        for b_key, b_members in self._bundle_groups.items():
            if b_key.startswith("canvas") and name in b_members:
                return True
        return False

    def _inject_intent_tools(self, query: str, names: List[str]) -> List[str]:
        """Pair related tools for known multi-step intents (e.g. YouTube find → display)."""
        expanded = list(names)
        q = (query or "").lower()
        youtube_music_intent = any(
            token in q
            for token in (
                "youtube music", "yt music", "ytm",
                "play music", "play some music", "put on music",
                "play me some", "put on some",
                "anime music", "lo-fi", "lofi", "listen to some",
                "soundtrack", " ost",
            )
        ) or ("playlist" in q and any(t in q for t in ("play", "put on", "music", "youtube")))
        video_explicit = any(
            token in q
            for token in (
                "youtube video", "yt video", "watch a video", "show me a video",
                "open a video", "find a video", "video about",
            )
        )
        if youtube_music_intent and not video_explicit:
            for tool_name in ("play_youtube_music", "load_skill"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        youtube_transcript_intent = any(
            token in q
            for token in (
                "transcript", "transcripts", "caption", "captions", "subtitle", "subtitles",
                "summarize video", "summarize the video", "summarize this video",
                "summary of this video", "summary of the video", "video summary",
                "read the video", "read this video", "read a video",
                "what's in this video", "what is in this video",
                "what happens in this video", "content of the video", "content of this video",
            )
        ) or (
            ("youtube.com" in q or "youtu.be" in q or "youtube" in q)
            and any(
                t in q
                for t in (
                    "transcript", "summary", "summarize", "read", "upgrade",
                    "what's in", "what is in", "what does this video say",
                    "things in this video", "in this video",
                )
            )
        )
        if youtube_transcript_intent:
            for tool_name in ("youtube_toolkit", "web_search"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        youtube_intent = any(
            token in q
            for token in (
                "youtube", "yt video", "watch a video", "show me a video",
                "open a video", "find a video",
            )
        )
        # Music requests should not also force the in-chat video card path.
        if youtube_intent and not youtube_music_intent:
            for tool_name in ("web_search", "show_youtube_video"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        # Kanban dispatch ("Hand to Chad") and any other kanban-board mention —
        # guarantee kanban_board is in context rather than relying on RAG/9B to
        # surface it, since a missed pick means the card silently never moves.
        kanban_intent = any(token in q for token in ("kanban board", "kanban card"))
        if kanban_intent and "kanban_board" not in expanded and "kanban_board" in self._tools:
            expanded.append("kanban_board")

        # Canvas LMS dispatch (assignments, syllabus, courses, grades, modules, homework) —
        # guarantee Canvas tools are seeded so _expand_bundles brings in the full bundle
        # and auto-loads the paired Canvas skill.
        import re
        canvas_intent = any(
            token in q
            for token in (
                "canvas", "assignment", "assignments", "coursework",
                "syllabus", "homework", "course file", "course files",
                "course grade", "course grades", "course module", "course modules",
                "lecture", "lectures", "lecture slide", "lecture slides", "course slides",
            )
        )
        is_explicit_canva = self._is_explicit_canva_query(q)
        if canvas_intent and not is_explicit_canva:
            # Respect negative constraints (e.g. "not assignments", "not the assingments")
            is_negative_assignment = any(
                neg in q
                for neg in (
                    "not assignment", "not assignments", "not the assignment", "not the assignments",
                    "no assignment", "no assignments", "stop assignment", "not homework", "no homework",
                    "not assingment", "not assingments", "not the assingments",
                )
            )
            if not is_negative_assignment:
                for tool_name in ("get_canvas_courses", "get_assignment_details", "get_canvas_assignments", "read_course_file"):
                    if tool_name not in expanded and tool_name in self._tools:
                        expanded.append(tool_name)
            else:
                for tool_name in ("get_canvas_courses", "read_course_file"):
                    if tool_name not in expanded and tool_name in self._tools:
                        expanded.append(tool_name)

            if any(token in q for token in ("file", "files", "download", "pdf", "docx")):
                for tool_name in ("read_course_file", "download_course_file"):
                    if tool_name not in expanded and tool_name in self._tools:
                        expanded.append(tool_name)

            if any(token in q for token in ("grade", "grades", "score", "scores")):
                for tool_name in ("get_course_grades", "get_recent_grades"):
                    if tool_name not in expanded and tool_name in self._tools:
                        expanded.append(tool_name)

            if any(token in q for token in (
                "module", "modules", "lecture", "lectures", "slide", "slides",
                "notes", "handout", "handouts", "reading", "readings",
            )):
                for tool_name in ("get_course_modules", "search_module_items", "get_course_files"):
                    if tool_name not in expanded and tool_name in self._tools:
                        expanded.append(tool_name)

        # Finance dispatch (spending, balance, net worth, transactions, SoFi, Capital One, Fidelity, Plaid, bank)
        finance_intent = any(
            token in q
            for token in (
                "sofi", "capital one", "fidelity", "plaid", "finance", "finances",
                "spending", "transactions", "transaction", "bank account", "bank accounts",
                "checking account", "savings account", "credit card", "credit cards",
                "net worth", "balance", "balances", "investment", "investments",
                "portfolio", "budget", "expenses", "spend", "subscription", "subscriptions",
                "recurring", "bill", "bills", "due date", "due dates", "minimum payment",
                "apr", "debt", "debts", "liability", "liabilities", "mortgage", "student loan",
                "dividend", "dividends", "trade", "trades",
            )
        )
        if finance_intent:
            for tool_name in ("get_financial_summary", "get_recent_transactions", "get_investment_portfolio", "render_chart"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

            if any(t in q for t in ("subscription", "subscriptions", "recurring", "bill", "bills")):
                if "get_recurring_subscriptions" not in expanded and "get_recurring_subscriptions" in self._tools:
                    expanded.append("get_recurring_subscriptions")

            if any(t in q for t in ("due date", "due dates", "minimum payment", "apr", "debt", "debts", "liability", "liabilities", "mortgage", "loan", "pay off")):
                if "get_liabilities" not in expanded and "get_liabilities" in self._tools:
                    expanded.append("get_liabilities")

            if any(t in q for t in ("dividend", "dividends", "trade", "trades", "buy", "sell", "stock trade")):
                if "get_investment_transactions" not in expanded and "get_investment_transactions" in self._tools:
                    expanded.append("get_investment_transactions")

            if any(t in q for t in ("refresh", "sync", "latest")):
                if "refresh_financial_data" not in expanded and "refresh_financial_data" in self._tools:
                    expanded.append("refresh_financial_data")

        # Canva graphic design dispatch (posters, flyers, marketing graphics, social posts)
        canva_intent = bool(re.search(r"\bcanva\b", q)) or (
            any(t in q for t in ("poster", "flyer", "social post", "marketing graphic", "brand template"))
            and not any(t in q for t in ("canvas", "course", "assignment", "syllabus", "homework", "lecture", "amazon", "buy", "cart", "shop", "order"))
        )
        if canva_intent:
            for tool_name in ("load_skill", "call_mcp_tool"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        # AI image generation dispatch (dog, cat, wallpaper, artwork, render image, drawing)
        image_intent = any(
            token in q
            for token in (
                "generate an image", "create an image", "make an image",
                "generate image", "create image", "make image",
                "draw a", "draw me", "paint a", "paint me",
                "picture of", "photo of", "illustration of", "artwork of",
                "render an image", "render a picture", "ai image", "ai art",
            )
        )
        if image_intent:
            for tool_name in ("generate_image", "load_skill"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        motion_intent = any(
            token in q
            for token in (
                "animation", "animated", "animate", "motion graphic", "motion design",
                "explainer video", "animated video", "css animation", "svg animation",
            )
        )
        if motion_intent:
            for tool_name in ("render_animation", "load_skill"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        email_send_intent = any(
            token in q
            for token in (
                "send an email", "send email", "email me", "email this",
                "email the link", "email them", "email it", "shoot me an email",
            )
        )
        if email_send_intent and "send_email" not in expanded and "send_email" in self._tools:
            expanded.append("send_email")

        # Google Drive / Docs / Sheets intent seeding
        drive_intent = any(
            token in q
            for token in (
                "google drive", "google docs", "google sheets", "google doc", "google sheet",
                "in drive", "on drive", "from drive", "my drive", "drive folder",
                "spreadsheet on google drive", "doc on google drive", "sheet on google drive",
            )
        ) or ("drive" in q and any(w in q for w in ("search", "find", "read", "open", "folder", "sheet", "doc", "file", "list", "spreadsheet")))
        if drive_intent:
            for tool_name in ("google_drive_search", "google_drive_read_file", "google_drive_list_folder"):
                if tool_name not in expanded and tool_name in self._tools:
                    expanded.append(tool_name)

        return expanded

    def _selector_fail_fast(self) -> bool:
        from src.shared.config_manager import ConfigManager
        if not ConfigManager.is_local_model_active():
            return False
        return bool(ConfigManager.get("tools.llm_selector_fail_fast", True))

    def _hybrid_enabled(self) -> bool:
        """Hybrid = deterministic RAG shortlist → 9B picks from it (vs 9B over the full catalog).

        Env CHAD_HYBRID_SELECTION (0/1) overrides config for easy A/B benchmarking.
        """
        import os
        env = os.getenv("CHAD_HYBRID_SELECTION")
        if env is not None:
            return env.strip() not in ("0", "false", "False", "")
        from src.shared.config_manager import ConfigManager
        return bool(ConfigManager.get("tools.hybrid_selection", True))

    def _pinned_menu(self, schemas: List[Any], seen_names: Set[str]) -> Optional[Dict[str, Any]]:
        """Resolve ``CHAD_PINNED_TOOLS`` into a finished result, or None if unpinned.

        Sits below the chat lane (which binds nothing at all by design) but above every
        other category, so an explicit pin overrides voice's fixed menu and the LLM
        selector alike. Core tools are already in ``schemas``; a pin ADDS to them rather
        than replacing them, so a pinned lane keeps the same always-on floor a real turn
        has. See :func:`pinned_tool_names` for why the pin exists at all (#675).
        """
        pinned = pinned_tool_names()
        if pinned is None:
            return None
        wanted = [n for n in self._expand_bundles(pinned) if n not in seen_names]
        missing = [n for n in wanted if self._tools.get(n) is None]
        if missing:
            logger.warning(f"[ TOOL_SELECT ] Pinned but not registered — skipped: {missing}")
        schemas += [self._extract_schema(n, self._tools[n]) for n in wanted if n not in missing]
        names = [s["name"] for s in schemas]
        logger.info(f"[ TOOL_SELECT ] Pinned menu (selector skipped): {names}")
        return {"schemas": schemas, "is_simple": False, "bundled_skills": [],
                "selection": {"path": "pinned", "final_tool_names": names}}

    def _selector_catalog(self, query: str, history: Optional[List[Any]],
                          selection_meta: Dict[str, Any],
                          knowledge_context: Optional[str] = None) -> Dict[str, Any]:
        """The tool dict handed to the 9B selector.

        Hybrid ON  → a generous, recall-oriented RAG shortlist (narrows the catalog without
                     dropping plausibly-needed tools), so the 9B chooses from a small, complete
                     menu — far less run-to-run variance than picking from the full catalog.
        Hybrid OFF → the full catalog (legacy behavior).
        Falls back to the full catalog if RAG yields nothing (e.g. Supabase unreachable).
        """
        from src.infrastructure.tools.delegate_routing import is_supervisor_delegate_only
        candidate_pool = {
            n: t for n, t in self._tools.items()
            if not is_supervisor_delegate_only(n, self._categories, self._tool_sources)
            and n not in self._core_tool_names
        }

        if not self._hybrid_enabled():
            selection_meta["selector_catalog"] = "full"
            return candidate_pool
        from src.shared.config_manager import ConfigManager
        import time as _time
        k = int(ConfigManager.get("tools.hybrid_shortlist_k", 20))
        _t0 = _time.perf_counter()
        shortlist_query = self._enrich_query_with_knowledge(query, knowledge_context)
        shortlist = self._selector.retrieve_shortlist(
            shortlist_query, candidate_pool, k=k, history=history
        )
        selection_meta["shortlist_ms"] = round((_time.perf_counter() - _t0) * 1000.0, 1)
        if not shortlist:
            selection_meta["selector_catalog"] = "full_fallback_empty_shortlist"
            logger.warning("[ TOOL_SELECT ] Hybrid shortlist empty — handing LLM selector candidate pool.")
            return candidate_pool
        sub_catalog = {n: candidate_pool[n] for n in shortlist if n in candidate_pool}
        if self._is_explicit_canva_query(query):
            sub_catalog = {n: t for n, t in sub_catalog.items() if not self._is_canvas_lms_tool(n)}
            for tool_name in ("call_mcp_tool", "load_skill"):
                if tool_name in candidate_pool and tool_name not in sub_catalog:
                    sub_catalog[tool_name] = candidate_pool[tool_name]
        q_lower = (query or "").lower()
        if any(token in q_lower for token in ("send an email", "send email", "email me", "email this", "email the link", "email it", "email them")):
            if "send_email" in candidate_pool and "send_email" not in sub_catalog:
                sub_catalog["send_email"] = candidate_pool["send_email"]
        if any(token in q_lower for token in ("google drive", "google sheet", "google doc", "in drive", "on drive", "from drive", "spreadsheet")):
            for tool_name in ("google_drive_search", "google_drive_read_file", "google_drive_list_folder"):
                if tool_name in candidate_pool and tool_name not in sub_catalog:
                    sub_catalog[tool_name] = candidate_pool[tool_name]
        if any(token in q_lower for token in ("transcript", "transcripts", "captions", "subtitles", "summarize video", "video summary")) or (
            ("youtube.com" in q_lower or "youtu.be" in q_lower or "youtube" in q_lower)
            and any(t in q_lower for t in ("transcript", "summary", "summarize", "read", "upgrade", "what's in", "what is in", "what does this video say", "things in this video", "in this video"))
        ):
            if "youtube_toolkit" in candidate_pool and "youtube_toolkit" not in sub_catalog:
                sub_catalog["youtube_toolkit"] = candidate_pool["youtube_toolkit"]
        selection_meta["selector_catalog"] = "hybrid_shortlist"
        selection_meta["shortlist_size"] = len(sub_catalog)
        selection_meta["shortlist"] = shortlist
        logger.info(f"[ TOOL_SELECT ] Hybrid shortlist ({len(sub_catalog)}/{len(candidate_pool)} "
                    f"tools) → LLM selector: {shortlist}")
        return sub_catalog

    @staticmethod
    def _enrich_query_with_knowledge(query: str, knowledge_context: Optional[str],
                                     *, max_chars: int = 400) -> str:
        """Append a compact knowledge snippet so RAG shortlist recall shifts toward
        tools matching personal context (e.g. email hits → Gmail tools)."""
        snippet = (knowledge_context or "").strip()
        if not snippet:
            return query
        # Prefer bullet content over section headers for embedding signal.
        lines = [
            ln.lstrip("- ").strip()
            for ln in snippet.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        compact = " ".join(lines)
        if len(compact) > max_chars:
            compact = compact[: max_chars - 3].rstrip() + "..."
        if not compact:
            return query
        return f"{query}\n{compact}"

    def _abort_selector_failure(self, reason: str, selection_meta: Dict[str, Any]) -> None:
        from src.shared.exceptions import ToolSelectionError
        selection_meta["path"] = "failed"
        selection_meta["llm_unavailable_reason"] = reason
        raise ToolSelectionError(
            f"LLM tool selector failed ({reason}). Turn aborted — no RAG fallback."
        )

    def _bundled_skills_for(self, tool_names: List[str], query: str = "") -> List[str]:
        """Return skill names to auto-load when a tool bundle is active this turn."""
        triggering_names = set(tool_names) - NON_TRIGGERING_BUNDLE_TOOLS
        skills: List[str] = []
        for bundle_key, members in self._bundle_groups.items():
            if not (triggering_names & members):
                continue
            skill_name = self._bundle_skills.get(bundle_key)
            if skill_name and skill_name not in skills:
                # Do not auto-load youtube_music skill without explicit music intent.
                # Semantic matches on "anime", "ambient", etc. in shopping/research queries
                # must not auto-load the 1.5k token youtube_music SKILL.md.
                if skill_name == "youtube_music":
                    q = (query or "").lower()
                    has_music_intent = any(
                        token in q
                        for token in (
                            "youtube music", "yt music", "ytm",
                            "play music", "play some music", "put on music",
                            "play me some", "put on some",
                            "anime music", "lo-fi", "lofi", "listen to some",
                            "soundtrack", " ost", "song", "songs", "album", "artist",
                        )
                    ) or ("playlist" in q and any(t in q for t in ("play", "put on", "music", "youtube")))
                    if not has_music_intent:
                        continue
                skills.append(skill_name)
        if self._is_explicit_canva_query(query):
            skills = [s for s in skills if s != "canvas"]
            if "canva" not in skills:
                skills.append("canva")
        return skills

    def _should_expose_mcp_directly(self, name: str, selector_picked: Set[str]) -> bool:
        """Expose MCP tools the selector (9B or RAG) chose for this query."""
        return name in selector_picked

    # --- main entry ---------------------------------------------------- #
    @traceable(run_type="chain", name="ToolRegistryInjector")
    def get_schemas_for_turn(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        category: Optional[str] = None,
        on_completions_start: Optional[Callable[[], None]] = None,
        knowledge_context: Optional[str] = None,
    ) -> Dict[str, List[Any]]:
        """Retrieves relevant tool schemas for a given user query."""
        schemas = []
        seen_names = set()
        is_simple = False
        selected_metadata: List[Dict[str, Any]] = []

        # 1. Always inject the small set of permanent core tools
        for core_name in self._core_tool_names:
            tool = self._tools.get(core_name)
            if tool:
                if hasattr(tool, 'get_schema'):
                    schemas.append(tool.get_schema())
                    seen_names.add(core_name)
                elif isinstance(tool, dict) and 'schema' in tool:
                    schemas.append(tool['schema'])
                    seen_names.add(core_name)
        logger.info(f"[ TOOL_SELECT ] Core tools: {list(seen_names)}")

        # Planner turns emit a structured plan; tools aren't bound to the planner call itself.
        # We STILL run the selector here so the tools discovered for the *original query* are
        # populated into state["tools"] and carry forward programmatically to the post-approval
        # execution turn (no re-discovery on "proceed"). Selector failure must not abort the
        # planner, so on planning turns the selector is non-fatal (falls back to RAG, then core).
        planning_turn = (category == "planning")
        voice_turn = (category == "voice")
        chat_turn = category in ("chat", "conversation")

        if chat_turn:
            logger.info("[ TOOL_SELECT ] Chat lane — skipping LLM/RAG tool selector.")
            schemas = []
            for name in (
                "web_search",
                "search_browser_history",
                "google_drive_search",
                "google_drive_list_folder",
                "google_drive_read_file",
                "get_calendar_events",
                "search_gmail",
                "read_gmail",
                "search_contacts",
                "get_financial_summary",
                "get_recent_transactions",
                "get_investment_portfolio",
                "get_recurring_subscriptions",
            ):
                tool = self._tools.get(name)
                if tool:
                    if hasattr(tool, "get_schema"):
                        schemas.append(tool.get_schema())
                    elif isinstance(tool, dict) and "schema" in tool:
                        schemas.append(tool["schema"])
            names = [s.get("name") for s in schemas if isinstance(s, dict)]
            logger.info(f"[ TOOL_SELECT ] Chat fixed read-only menu: {names}")
            return {
                "schemas": schemas,
                "is_simple": True,
                "selection": {"path": "turn_mode_chat", "final_tool_names": names},
                "bundled_skills": [],
            }

        pinned_result = self._pinned_menu(schemas, seen_names)
        if pinned_result is not None:
            return pinned_result

        if voice_turn:
            from src.core.orchestration.nodes.voice_node import VOICE_TOOL_NAMES
            schemas = []
            for name in VOICE_TOOL_NAMES:
                tool = self._tools.get(name)
                if tool and hasattr(tool, "get_schema"):
                    schemas.append(tool.get_schema())
            names = [s.get("name") for s in schemas if isinstance(s, dict)]
            logger.info(f"[ TOOL_SELECT ] Voice fixed menu: {names}")
            return {
                "schemas": schemas,
                "is_simple": True,
                "selection": {"path": "voice_fixed", "final_tool_names": names},
                "bundled_skills": [],
            }

        # 2. Try LLM selector (9B) first — falls back to RAG if unavailable
        llm_names = None
        selection_meta: Dict[str, Any] = {
            "path": None,
            "llm_available": False,
            "llm_unavailable_reason": None,
            "llm_model": None,
            "llm_selected": [],
            "llm_select_meta": {},
            "rag_selected": [],
            "final_tool_names": [],
            "mcp_tools_in_final": [],
            "had_knowledge_context": bool((knowledge_context or "").strip()),
        }
        llm_available = self._llm_selector.is_available()
        selection_meta["llm_available"] = llm_available
        selection_meta["llm_model"] = getattr(self._llm_selector, "_resolved_model_id", None) or getattr(
            self._llm_selector, "_model_id", None
        )

        if llm_available:
            catalog = self._selector_catalog(
                query, history, selection_meta, knowledge_context=knowledge_context
            )
            llm_names = self._llm_selector.select(
                query,
                catalog,
                history=history,
                on_completions_start=on_completions_start,
                knowledge_context=knowledge_context,
                core_tools=list(self._core_tool_names),
            )
            if self._registry._event_bus:
                from src.application.events.events import StatusUpdate
                from src.core.orchestration.turn.status_labels import FORMULATING_LABELS
                import random
                self._registry._event_bus.publish(StatusUpdate(status=random.choice(FORMULATING_LABELS)))
            selection_meta["llm_select_meta"] = self._llm_selector.last_select_meta

        is_canva = self._is_explicit_canva_query(query)

        if llm_names is not None:
            if is_canva:
                llm_names = [n for n in llm_names if not self._is_canvas_lms_tool(n)]
            selection_meta["path"] = "llm"
            selection_meta["llm_selected"] = list(llm_names)
            seeded = self._inject_intent_tools(query, llm_names)
            candidate_names = self._expand_bundles(seeded)
            if is_canva:
                candidate_names = [n for n in candidate_names if not self._is_canvas_lms_tool(n)]
            logger.info(f"[ TOOL_SELECT ] LLM selected {len(llm_names)} tools: {llm_names}")
            if candidate_names != llm_names:
                logger.info(f"[ TOOL_SELECT ] After bundle expansion: {candidate_names}")
        else:
            reason = self._llm_selector.last_unavailable_reason or (
                "unknown" if not llm_available else "select_returned_none"
            )
            selection_meta["llm_unavailable_reason"] = reason
            if self._selector_fail_fast() and not planning_turn:
                self._abort_selector_failure(reason, selection_meta)
            selection_meta["path"] = "rag"
            if not llm_available:
                logger.warning(
                    f"[ TOOL_SELECT ] LLM selector unavailable ({reason}) — falling back to RAG."
                )
            else:
                logger.warning(
                    f"[ TOOL_SELECT ] LLM selector call failed ({reason}) — falling back to RAG."
                )
            selected_metadata = self._selector.retrieve_relevant_tools(
                query, self._tools, history=history
            )
            rag_names = [m.get('name') for m in selected_metadata]
            if is_canva:
                rag_names = [n for n in rag_names if not self._is_canvas_lms_tool(n)]
            selection_meta["rag_selected"] = rag_names
            logger.info(f"[ TOOL_SELECT ] RAG returned: {rag_names}")
            is_simple = any(m.get('source') == 'local_simple' for m in selected_metadata)

            from src.shared.config_manager import ConfigManager
            limit = ConfigManager.get("tools.rag_tool_limit", 8)
            seeded = self._inject_intent_tools(query, rag_names[:limit])
            candidate_names = self._expand_bundles(seeded)
            if is_canva:
                candidate_names = [n for n in candidate_names if not self._is_canvas_lms_tool(n)]

        selector_picked = set(candidate_names)

        # 3. Resolve candidates to schemas, applying category exclusion rules
        for name in candidate_names:
            if name in seen_names:
                continue

            # MCP: expose directly when the selector picked it for this query
            if self._tool_sources.get(name) == 'mcp':
                if not self._should_expose_mcp_directly(name, selector_picked):
                    logger.debug(f"[ TOOL_SELECT ] Skipping MCP tool '{name}' (not selector-picked).")
                    continue
                logger.info(f"[ TOOL_SELECT ] Direct MCP exposure: {name}")

            # Skills must only load via explicit load_skill trigger
            if name in self._categories.get('skills', []):
                logger.debug(f"[ TOOL_SELECT ] Skipping skill '{name}' (load_skill only).")
                continue

            # Coding / specialist categories → delegate_to_* handoffs only.
            # Drive + local FS atoms are bindable for known-target / quick lookups
            # (explore still goes through delegate_to_traversal_agent via prompts).
            from src.infrastructure.tools.delegate_routing import is_supervisor_delegate_only
            if not planning_turn and is_supervisor_delegate_only(name, self._categories, self._tool_sources):
                logger.debug(f"[ TOOL_SELECT ] Excluding '{name}' to force subagent delegation.")
                continue

            tool = self._tools.get(name)
            if tool:
                schemas.append(self._extract_schema(name, tool))
                seen_names.add(name)
            else:
                # RAG fallback: cloud schema may be available even if tool isn't local
                if llm_names is None:
                    meta = next((m for m in selected_metadata if m.get('name') == name), {})
                    if 'schema' in meta:
                        logger.info(f"[ TOOL_SELECT ] '{name}' from cloud schema (not local).")
                        schemas.append(meta['schema'])
                        seen_names.add(name)
                    else:
                        logger.warning(f"[ TOOL_SELECT ] '{name}' has no schema and no local instance.")

        logger.info(f"[ TOOL_SELECT ] Final tool set ({len(schemas)}): {[s['name'] for s in schemas]} (is_simple={is_simple})")
        if not schemas:
            logger.warning(f"[ TOOL_SELECT ] No schemas found for query: {query!r}")

        final_names = [s['name'] for s in schemas]
        selection_meta["final_tool_names"] = final_names
        selection_meta["core_tool_names"] = [n for n in final_names if n in self._core_tool_names]
        selection_meta["extra_tool_names"] = [n for n in final_names if n not in self._core_tool_names]
        selection_meta["mcp_tools_in_final"] = [
            n for n in final_names if self._tool_sources.get(n) == "mcp"
        ]
        # Use candidate_names (post bundle-expansion, pre delegate-only exclusion) rather
        # than final_names — Gmail/coding bundle members may be supervisor-delegate-only
        # and never land in the schemas list, but the bundle is still "active" for this
        # turn and the paired SKILL.md should still auto-load for whichever agent executes it.
        bundled_skills = self._bundled_skills_for(list(candidate_names), query=query)
        if bundled_skills:
            selection_meta["bundled_skills"] = bundled_skills
            logger.info(f"[ TOOL_SELECT ] Bundled skills: {bundled_skills}")

        return {
            "schemas": schemas,
            "is_simple": is_simple,
            "selection": selection_meta,
            "bundled_skills": bundled_skills,
        }
