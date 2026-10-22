"""TurnToolSelector — hybrid RAG->9B shortlisting and LM-Studio-unreachable fallback.

Covers two related issue #216 gaps:
- "HybridToolSelector: RAG returns shortlist -> 9B picks from it; 9B unreachable -> RAG fallback"
- "LM Studio unreachable: tool selection falls back to pure RAG, not crash"

get_schemas_for_turn is the single entry point both paths flow through
(src/infrastructure/tools/selection/turn_selector.py), so these tests drive it directly
against a minimal fake registry rather than a real ToolRegistry + Supabase + LM Studio.
"""
from unittest.mock import patch

import pytest

from src.infrastructure.tools.selection.turn_selector import TurnToolSelector
from src.shared.exceptions import ToolSelectionError


def _tool(name):
    return {"schema": {"name": name, "description": name, "parameters": {}}}


class _FakeSemanticSelector:
    def __init__(self, shortlist=None, rag_results=None):
        self._shortlist = shortlist if shortlist is not None else []
        self._rag_results = rag_results if rag_results is not None else []
        self.shortlist_calls = []
        self.rag_calls = []

    def retrieve_shortlist(self, query, tools, k=50, history=None):
        self.shortlist_calls.append(query)
        return self._shortlist

    def retrieve_relevant_tools(self, query, tools, history=None):
        self.rag_calls.append(query)
        return self._rag_results


class _FakeLLMSelector:
    def __init__(self, available=True, picks=None, unavailable_reason=None):
        self._available = available
        self._picks = picks if picks is not None else []
        self.last_unavailable_reason = unavailable_reason
        self.last_select_meta = {}
        self.select_calls = []

    def is_available(self):
        return self._available

    def select(self, query, catalog, history=None, on_completions_start=None,
               knowledge_context=None, core_tools=None):
        self.select_calls.append({
            "query": query,
            "catalog": set(catalog.keys()),
            "history": history,
            "knowledge_context": knowledge_context,
            "core_tools": core_tools,
        })
        if not self._available:
            return None
        return self._picks


class _FakeRegistry:
    def __init__(self, tools, llm_selector, semantic_selector):
        self._tools = tools
        self._categories = {"skills": []}
        self._tool_sources = {}
        self._core_tool_names = set()
        self._selector = semantic_selector
        self._llm_selector = llm_selector
        self._bundle_groups = {}
        self._bundle_skills = {}
        self._event_bus = None
        self._schema_builder = _SchemaBuilder()


class _SchemaBuilder:
    def extract_schema(self, name, tool):
        return tool["schema"]


def _config_get(defaults):
    def _get(key, default=None):
        return defaults.get(key, default)
    return _get


@pytest.fixture(autouse=True)
def _default_config(monkeypatch):
    """hybrid_selection ON, fail_fast OFF by default — individual tests override as needed."""
    from src.shared.config_manager import ConfigManager
    defaults = {
        "tools.hybrid_selection": True,
        "tools.hybrid_shortlist_k": 50,
        "tools.llm_selector_fail_fast": False,
        "tools.rag_tool_limit": 8,
    }
    monkeypatch.setattr(ConfigManager, "get", staticmethod(_config_get(defaults)))
    monkeypatch.setattr(ConfigManager, "is_local_model_active", staticmethod(lambda: True))
    return defaults


def test_hybrid_shortlist_feeds_9b_which_picks_from_it():
    tools = {n: _tool(n) for n in ("web_search", "get_datetime", "kanban_board", "unrelated_tool")}
    semantic = _FakeSemanticSelector(shortlist=["web_search", "get_datetime"])
    llm = _FakeLLMSelector(available=True, picks=["web_search"])
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    result = selector.get_schemas_for_turn("what's today's weather", category="agent")

    assert result["selection"]["path"] == "llm"
    assert result["selection"]["selector_catalog"] == "hybrid_shortlist"
    # The 9B must only have seen the shortlist, not the full catalog.
    assert llm.select_calls[0]["catalog"] == {"web_search", "get_datetime"}
    assert result["selection"]["final_tool_names"] == ["web_search"]


def test_empty_shortlist_falls_back_to_full_catalog_for_9b():
    tools = {n: _tool(n) for n in ("web_search", "get_datetime")}
    semantic = _FakeSemanticSelector(shortlist=[])
    llm = _FakeLLMSelector(available=True, picks=["get_datetime"])
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    result = selector.get_schemas_for_turn("do a thing", category="agent")

    assert result["selection"]["selector_catalog"] == "full_fallback_empty_shortlist"
    assert llm.select_calls[0]["catalog"] == set(tools.keys())


def test_9b_unreachable_falls_back_to_rag_not_a_crash():
    tools = {n: _tool(n) for n in ("web_search", "get_datetime")}
    semantic = _FakeSemanticSelector(rag_results=[{"name": "web_search"}])
    llm = _FakeLLMSelector(available=False, unavailable_reason="connection_refused")
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    result = selector.get_schemas_for_turn("search the web", category="agent")

    assert result["selection"]["path"] == "rag"
    assert result["selection"]["llm_unavailable_reason"] == "connection_refused"
    assert result["selection"]["final_tool_names"] == ["web_search"]
    assert semantic.rag_calls == ["search the web"]


def test_fail_fast_raises_when_9b_unreachable_on_a_normal_turn():
    tools = {n: _tool(n) for n in ("web_search",)}
    semantic = _FakeSemanticSelector(rag_results=[{"name": "web_search"}])
    llm = _FakeLLMSelector(available=False, unavailable_reason="connection_refused")
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    with patch(
        "src.shared.config_manager.ConfigManager.get",
        staticmethod(_config_get({
            "tools.hybrid_selection": True,
            "tools.llm_selector_fail_fast": True,
        })),
    ):
        with pytest.raises(ToolSelectionError):
            selector.get_schemas_for_turn("search the web", category="agent")


def test_fail_fast_does_not_abort_a_planning_turn():
    """Selector failure must never abort planning — see turn_selector.py's comment on
    planning_turn: tools still populate for the post-approval execution turn via RAG."""
    tools = {n: _tool(n) for n in ("web_search",)}
    semantic = _FakeSemanticSelector(rag_results=[{"name": "web_search"}])
    llm = _FakeLLMSelector(available=False, unavailable_reason="connection_refused")
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    with patch(
        "src.shared.config_manager.ConfigManager.get",
        staticmethod(_config_get({
            "tools.hybrid_selection": True,
            "tools.llm_selector_fail_fast": True,
            "tools.rag_tool_limit": 8,
        })),
    ):
        result = selector.get_schemas_for_turn("search the web", category="planning")

    assert result["selection"]["path"] == "rag"
    assert result["selection"]["final_tool_names"] == ["web_search"]


def test_knowledge_context_forwarded_to_llm_and_enriches_shortlist_query():
    """Activity/email knowledge must reach the 9B and shift the RAG shortlist query."""
    tools = {n: _tool(n) for n in ("search_gmail", "open_app", "web_search")}
    semantic = _FakeSemanticSelector(shortlist=["search_gmail", "web_search"])
    llm = _FakeLLMSelector(available=True, picks=["search_gmail"])
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    knowledge = (
        "\n\n### ACTIVITY CONTEXT (PERSONAL INDEX)\n"
        "- Email: 'Stipend Rate Increase Request' from Smart Scholar\n"
    )
    history = [{"role": "user", "content": "earlier ask"}]
    result = selector.get_schemas_for_turn(
        "find what the next step for stipend increase request is",
        history=history,
        category="agent",
        knowledge_context=knowledge,
    )

    assert result["selection"]["path"] == "llm"
    assert result["selection"]["had_knowledge_context"] is True
    assert llm.select_calls[0]["knowledge_context"] == knowledge
    assert llm.select_calls[0]["history"] == history
    # Shortlist query should include email signal from knowledge, not bare user text only.
    assert "Stipend Rate Increase Request" in semantic.shortlist_calls[0]
    assert "stipend increase" in semantic.shortlist_calls[0].lower()


@pytest.mark.asyncio
async def test_orchestration_turn_selector_passes_history_and_knowledge():
    """Orchestration TurnToolSelector must stop hardcoding history=None and forward knowledge."""
    from src.core.orchestration.turn.tool_selector import TurnToolSelector as OrchSelector

    captured = {}

    class _Reg:
        def get_schemas_for_turn(self, query, history=None, category=None,
                                 on_completions_start=None, knowledge_context=None):
            captured["query"] = query
            captured["history"] = history
            captured["category"] = category
            captured["knowledge_context"] = knowledge_context
            return {
                "schemas": [{"name": "search_gmail"}],
                "is_simple": False,
                "selection": {"path": "llm", "final_tool_names": ["search_gmail"]},
                "bundled_skills": [],
            }

    orch = OrchSelector(_Reg())
    history = [{"role": "user", "content": "prior"}]
    knowledge = "### ACTIVITY CONTEXT\n- Email about stipend"
    result = await orch.select(
        "next step for stipend",
        category="agent",
        history=history,
        knowledge_context=knowledge,
    )
    assert captured["history"] == history
    assert captured["knowledge_context"] == knowledge
    assert result.selection["final_tool_names"] == ["search_gmail"]


# --- pinned tool menu (#675) ------------------------------------------------ #
# The 9B selector doesn't fit alongside the 26B supervisor in the VRAM budget, so on a
# single-GPU box every selector call evicts the supervisor and pays 15-20s to reload it.
# CHAD_PINNED_TOOLS lets a lane that isn't measuring tool selection skip the 9B entirely.


def test_pinned_tools_skip_the_llm_selector_entirely(monkeypatch):
    monkeypatch.setenv("CHAD_PINNED_TOOLS", "manage_user_memory,get_datetime")
    tools = {n: _tool(n) for n in ("manage_user_memory", "get_datetime", "web_search")}
    semantic = _FakeSemanticSelector(shortlist=["web_search"])
    llm = _FakeLLMSelector(available=True, picks=["web_search"])
    selector = TurnToolSelector(_FakeRegistry(tools, llm, semantic))

    result = selector.get_schemas_for_turn("we moved to Portland last month", category="agent")

    assert result["selection"]["path"] == "pinned"
    assert result["selection"]["final_tool_names"] == ["manage_user_memory", "get_datetime"]
    # The whole point: no 9B call, so no load, so no eviction of the supervisor.
    assert llm.select_calls == []
    assert semantic.shortlist_calls == []


def test_pinned_tools_include_core_tools_and_ignore_unknown_names(monkeypatch):
    monkeypatch.setenv("CHAD_PINNED_TOOLS", "get_datetime,does_not_exist")
    tools = {n: _tool(n) for n in ("get_datetime", "always_on")}
    registry = _FakeRegistry(tools, _FakeLLMSelector(), _FakeSemanticSelector())
    registry._core_tool_names = {"always_on"}
    selector = TurnToolSelector(registry)

    result = selector.get_schemas_for_turn("anything", category="agent")

    # Core floor is preserved; an unregistered pin is skipped, not fatal.
    assert result["selection"]["final_tool_names"] == ["always_on", "get_datetime"]


def test_empty_pin_means_core_tools_only_not_normal_selection(monkeypatch):
    """Empty string is meaningful — "bind nothing extra" — and distinct from unset."""
    monkeypatch.setenv("CHAD_PINNED_TOOLS", "")
    tools = {n: _tool(n) for n in ("web_search",)}
    llm = _FakeLLMSelector(available=True, picks=["web_search"])
    selector = TurnToolSelector(_FakeRegistry(tools, llm, _FakeSemanticSelector()))

    result = selector.get_schemas_for_turn("anything", category="agent")

    assert result["selection"]["path"] == "pinned"
    assert result["selection"]["final_tool_names"] == []
    assert llm.select_calls == []


def test_unset_pin_leaves_normal_selection_untouched(monkeypatch):
    monkeypatch.delenv("CHAD_PINNED_TOOLS", raising=False)
    tools = {n: _tool(n) for n in ("web_search",)}
    llm = _FakeLLMSelector(available=True, picks=["web_search"])
    selector = TurnToolSelector(_FakeRegistry(tools, llm, _FakeSemanticSelector(shortlist=["web_search"])))

    result = selector.get_schemas_for_turn("anything", category="agent")

    assert result["selection"]["path"] == "llm"
    assert len(llm.select_calls) == 1


def test_inject_intent_tools_seeds_canvas_modules_on_lecture_tokens():
    canvas_tool_names = (
        "get_canvas_courses", "get_assignment_details", "get_canvas_assignments",
        "read_course_file", "get_course_files", "download_course_file",
        "get_course_modules", "search_module_items"
    )
    tools = {n: _tool(n) for n in canvas_tool_names}
    selector = TurnToolSelector(_FakeRegistry(tools, _FakeLLMSelector(), _FakeSemanticSelector()))
    query = "go to my gemini notebook .com for wireless networks and add any new lectures pdfs from canvas"
    seeded = selector._inject_intent_tools(query, [])

    assert "get_course_modules" in seeded
    assert "search_module_items" in seeded
    assert "get_course_files" in seeded
    assert "download_course_file" in seeded
    assert "get_canvas_courses" in seeded


def test_inject_intent_tools_negative_assignment_constraint():
    canvas_tool_names = (
        "get_canvas_courses", "get_assignment_details", "get_canvas_assignments",
        "read_course_file", "get_course_files", "download_course_file",
        "get_course_modules", "search_module_items"
    )
    tools = {n: _tool(n) for n in canvas_tool_names}
    selector = TurnToolSelector(_FakeRegistry(tools, _FakeLLMSelector(), _FakeSemanticSelector()))
    query = "download the lectures from canvas, not assignments"
    seeded = selector._inject_intent_tools(query, [])

    assert "get_course_modules" in seeded
    assert "search_module_items" in seeded
    assert "get_assignment_details" not in seeded
    assert "get_canvas_assignments" not in seeded
    assert "get_upcoming_assignments" not in seeded


def test_search_tools_expands_cohesive_bundles():
    from src.infrastructure.tools.registry import ToolRegistry
    registry = ToolRegistry()
    registry._bundle_groups = {
        "canvas_modules": {
            "get_course_modules",
            "get_module_items",
            "search_module_items",
        }
    }
    registry._tools = {
        "get_course_modules": {"name": "get_course_modules", "schema": {"name": "get_course_modules"}},
        "get_module_items": {"name": "get_module_items", "schema": {"name": "get_module_items"}},
        "search_module_items": {"name": "search_module_items", "schema": {"name": "search_module_items"}},
    }
    # Mock selector retrieve_relevant_tools to return only get_course_modules
    registry._selector = _FakeSemanticSelector(rag_results=[{"name": "get_course_modules"}])

    results = registry.search_tools("modules", expand_bundles=True)
    result_names = {r["name"] for r in results}

    assert "get_course_modules" in result_names
    assert "get_module_items" in result_names
    assert "search_module_items" in result_names


def test_canva_query_disambiguation_and_bundled_skill():
    tool_names = (
        "get_canvas_courses", "get_assignment_details",
        "call_mcp_tool", "load_skill", "web_search",
    )
    tools = {n: _tool(n) for n in tool_names}
    fake_llm = _FakeLLMSelector(picks=["get_canvas_courses", "get_assignment_details"])
    fake_rag = _FakeSemanticSelector()
    registry = _FakeRegistry(tools, fake_llm, fake_rag)
    registry._bundle_groups = {
        "canvas": {"get_canvas_courses", "get_assignment_details"},
    }
    registry._bundle_skills = {"canvas": "canvas"}
    registry._categories = {"canvas": ["get_canvas_courses", "get_assignment_details"]}

    selector = TurnToolSelector(registry)
    query = "use canva to generate a spikeball club poster"
    result = selector.get_schemas_for_turn(query)

    schema_names = [s["name"] for s in result["schemas"]]
    assert "get_canvas_courses" not in schema_names
    assert "get_assignment_details" not in schema_names
    assert "call_mcp_tool" in schema_names
    assert "load_skill" in schema_names
    assert result["bundled_skills"] == ["canva"]


def test_canvas_lms_query_preserves_canvas_tools(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", lambda k, default=None: ["canvas_agent"] if k == "disabled_subagents" else default)
    tool_names = (
        "get_canvas_courses", "get_assignment_details",
        "call_mcp_tool", "load_skill",
    )
    tools = {n: _tool(n) for n in tool_names}
    fake_llm = _FakeLLMSelector(picks=["get_canvas_courses", "get_assignment_details"])
    fake_rag = _FakeSemanticSelector()
    registry = _FakeRegistry(tools, fake_llm, fake_rag)
    registry._bundle_groups = {
        "canvas": {"get_canvas_courses", "get_assignment_details"},
    }
    registry._bundle_skills = {"canvas": "canvas"}
    registry._categories = {"canvas": ["get_canvas_courses", "get_assignment_details"]}

    selector = TurnToolSelector(registry)
    query = "check my canvas assignments"
    result = selector.get_schemas_for_turn(query)

    schema_names = [s["name"] for s in result["schemas"]]
    assert "get_canvas_courses" in schema_names
    assert "get_assignment_details" in schema_names
    assert result["bundled_skills"] == ["canvas"]


def test_selector_catalog_prunes_canvas_tools_for_canva_queries():
    tool_names = (
        "get_canvas_courses", "read_course_file", "web_search",
        "call_mcp_tool", "load_skill",
    )
    tools = {n: _tool(n) for n in tool_names}
    fake_rag = _FakeSemanticSelector(shortlist=["get_canvas_courses", "read_course_file", "web_search"])
    registry = _FakeRegistry(tools, _FakeLLMSelector(), fake_rag)
    registry._bundle_groups = {
        "canvas": {"get_canvas_courses"},
        "canvas_files": {"read_course_file"},
    }
    registry._categories = {"canvas": ["get_canvas_courses", "read_course_file"]}

    selector = TurnToolSelector(registry)
    meta = {}
    catalog = selector._selector_catalog("use canva to generate a spikeball club poster", None, meta)

    assert "get_canvas_courses" not in catalog
    assert "read_course_file" not in catalog
    assert "call_mcp_tool" in catalog
    assert "load_skill" in catalog
    assert "web_search" in catalog


def test_image_generation_intent_seeds_generate_image():
    tools = {n: _tool(n) for n in ("generate_image", "load_skill", "web_search")}
    registry = _FakeRegistry(tools, _FakeLLMSelector(), _FakeSemanticSelector())
    selector = TurnToolSelector(registry)

    injected = selector._inject_intent_tools("generate an image of a dog", [])
    assert "generate_image" in injected
    assert "load_skill" in injected


def test_selector_catalog_excludes_delegate_only_tools():
    tool_names = (
        "web_search", "browser_navigate", "browser_click", "open_browser",
        "write_file", "ui_click", "search_browser_history",
    )
    tools = {n: _tool(n) for n in tool_names}
    fake_rag = _FakeSemanticSelector(shortlist=list(tool_names))
    registry = _FakeRegistry(tools, _FakeLLMSelector(), fake_rag)
    registry._tool_sources = {n: "mcp" if n.startswith("browser_") else "local" for n in tool_names}

    selector = TurnToolSelector(registry)
    meta = {}
    catalog = selector._selector_catalog("search for anime poster on amazon", None, meta)

    # Specialist-internal tools must NEVER be in the supervisor's selector catalog
    assert "browser_navigate" not in catalog
    assert "browser_click" not in catalog
    assert "open_browser" not in catalog
    assert "write_file" not in catalog
    assert "ui_click" not in catalog

    # Direct supervisor tools must remain
    assert "web_search" in catalog
    assert "search_browser_history" in catalog


def test_bundled_skills_youtube_music_requires_music_intent():
    tools = {n: _tool(n) for n in ("play_youtube_music", "web_search")}
    registry = _FakeRegistry(tools, _FakeLLMSelector(), _FakeSemanticSelector())
    registry._bundle_groups = {"youtube_music": {"play_youtube_music", "load_skill"}}
    registry._bundle_skills = {"youtube_music": "youtube_music"}

    selector = TurnToolSelector(registry)

    # Query without music intent (e.g. shopping for anime merchandise)
    skills = selector._bundled_skills_for(
        ["play_youtube_music"],
        query="find me an anime poster on amazon and add it to cart"
    )
    assert "youtube_music" not in skills

    # Query with explicit music intent
    skills_music = selector._bundled_skills_for(
        ["play_youtube_music"],
        query="play some anime music for studying"
    )
    assert "youtube_music" in skills_music


def test_mcp_specialist_tools_excluded_from_supervisor_catalog():
    """Tools from connected MCP servers (Canva, Excalidraw, GitHub, Todoist, etc.)
    must not leak into the supervisor's candidate pool or RAG shortlist."""
    tool_names = (
        "web_search", "send_email", "clear_canvas", "describe_scene", "get-assets",
        "search_repositories", "add_task", "fetch_markdown",
    )
    tools = {n: _tool(n) for n in tool_names}
    fake_rag = _FakeSemanticSelector(shortlist=list(tool_names))
    registry = _FakeRegistry(tools, _FakeLLMSelector(), fake_rag)
    registry._tool_sources = {
        "web_search": "local",
        "send_email": "local",
        "clear_canvas": "mcp",
        "describe_scene": "mcp",
        "get-assets": "mcp",
        "search_repositories": "mcp",
        "add_task": "mcp",
        "fetch_markdown": "mcp",
    }
    registry._categories = {
        "skills": [],
        "excalidraw": ["clear_canvas", "describe_scene"],
        "canva": ["get-assets"],
        "github": ["search_repositories"],
        "todoist": ["add_task"],
        "fetch": ["fetch_markdown"],
    }

    selector = TurnToolSelector(registry)
    meta = {}
    catalog = selector._selector_catalog(
        "use the browser to find me a cool anime poster from an anime i like on amazon.com, add it to my cart and email me the link",
        None,
        meta,
    )

    # MCP tools must NEVER leak into the supervisor's selector catalog
    assert "clear_canvas" not in catalog
    assert "describe_scene" not in catalog
    assert "get-assets" not in catalog
    assert "search_repositories" not in catalog
    assert "add_task" not in catalog
    assert "fetch_markdown" not in catalog

    # Native supervisor tools must remain
    assert "web_search" in catalog
    assert "send_email" in catalog


def test_inject_intent_tools_youtube_transcript_vs_display():
    tools = {n: _tool(n) for n in ("youtube_toolkit", "show_youtube_video", "web_search")}
    registry = _FakeRegistry(tools, _FakeLLMSelector(), _FakeSemanticSelector())
    selector = TurnToolSelector(registry)

    # 1. Transcript / summary intent -> injects youtube_toolkit
    transcript_query = "does chad have the ability to read a youtube transcript? specifically: identify upgrades: https://www.youtube.com/watch?v=tDWtcyeNNew"
    injected_transcript = selector._inject_intent_tools(transcript_query, [])
    assert "youtube_toolkit" in injected_transcript
    assert "web_search" in injected_transcript

    # 2. Display-only intent -> show_youtube_video, NOT youtube_toolkit
    display_query = "show me a youtube video about skyrizi"
    injected_display = selector._inject_intent_tools(display_query, [])
    assert "show_youtube_video" in injected_display
    assert "youtube_toolkit" not in injected_display




