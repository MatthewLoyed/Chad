import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import HumanMessage

from src.core.orchestration.nodes import is_conversational, make_graphrag_node, build_knowledge_query
from src.core.intelligence import graph_rag_client as graph_rag_client_module
from src.core.intelligence.graph_rag_client import GraphRAGClient
from src.core.intelligence.graph_retrieval import GraphRetriever

# Deterministic seed facts for GraphRAGClient tests — must NOT depend on the
# developer's real data/knowledge_graph.json / config/personal_knowledge.md.
# Those are gitignored, machine-specific, private files (see docs/guides/PRIVATE_DATA.md)
# that drift over time; a unit test asserting on their content is inherently non-hermetic.
_TEST_SEED_FACTS = [
    {"id": "hardware_specs", "topic": "hardware", "content": "The user's desktop runs an AMD Ryzen CPU."},
    {"id": "budget_overview", "topic": "budget", "content": "The user is tracking a wedding budget."},
]


@pytest.fixture
async def isolated_graph_client(tmp_path, monkeypatch):
    """GraphRAGClient backed by a throwaway graph file + fixed seed facts."""
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: (_TEST_SEED_FACTS, [], []),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", _TEST_SEED_FACTS)
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", [])

    from src.shared.config_manager import ConfigManager
    graph_path = str(tmp_path / "test_knowledge_graph.json")
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()
    # Test retrieval/ranking with deterministic vectors, not a multi-GB cached
    # model. Real optional-asset download/offline load is verified separately.
    monkeypatch.setattr(client, "_get_embedding", lambda text:
                        [1.0, 0.0] if any(word in text.lower() for word in ("cpu", "hardware", "ryzen")) else [0.0, 1.0])
    return client


# ---------------------------------------------------------------------------
# is_conversational heuristic
# ---------------------------------------------------------------------------

def test_is_conversational_greetings():
    assert is_conversational([HumanMessage(content="hey")]) is True
    assert is_conversational([HumanMessage(content="thanks!")]) is True
    assert is_conversational([HumanMessage(content="ok cool")]) is True


def test_is_conversational_real_queries():
    assert is_conversational([HumanMessage(content="How is my wedding budget looking?")]) is False
    assert is_conversational([HumanMessage(content="what is the weather today")]) is False
    assert is_conversational([HumanMessage(content="search for flights to Atlanta")]) is False


def test_build_knowledge_query_expands_anaphoric_followup():
    messages = [
        HumanMessage(content="What's my fiancée's name?"),
        HumanMessage(content="whats the next step on our roadmap"),
    ]
    q = build_knowledge_query(messages)
    assert "fiancée" in q.lower() or "fiancee" in q.lower()
    assert "roadmap" in q.lower()
    assert "name" in q.lower()


def test_entity_tokens_split_camel_case():
    tokens = GraphRetriever.entity_tokens("Document:WeddingRoadmap")
    assert "wedding" in tokens
    assert "roadmap" in tokens


@pytest.mark.asyncio
async def test_node_uses_prior_turn_for_knowledge_search():
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- WeddingRoadmap -about-> Wedding")
    drive_client = MagicMock()
    drive_client.search = AsyncMock(return_value="- Drive file: 'Wedding Roadmap'")

    node = make_graphrag_node(graph_client, drive_client=drive_client)
    state = {
        "messages": [
            HumanMessage(content="Tell me about Sam and our wedding"),
            HumanMessage(content="whats the next step on our roadmap"),
        ],
        "system_instruction": "base",
    }
    result = await node(state, config=None)
    called_q = graph_client.search.await_args.args[0]
    assert "wedding" in called_q.lower()
    assert "roadmap" in called_q.lower()
    assert "DOCUMENT CONTEXT (DRIVE)" in result["knowledge_boost"]
    assert "RELATIONAL CONTEXT (GRAPH)" in result["knowledge_boost"]


# ---------------------------------------------------------------------------
# GraphRAGClient — knowledge graph search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_graph_client_retrieval(isolated_graph_client):
    result = await isolated_graph_client.search("hardware specs")
    assert result is not None
    assert "Ryzen" in result


@pytest.mark.asyncio
async def test_graph_client_relevant_query_scores_higher_than_irrelevant(isolated_graph_client):
    """Relevant personal queries should score higher than random off-topic ones."""
    # This should always hit — hardware topic has explicit seed data
    hardware_result = await isolated_graph_client.search("what CPU does the user have")
    assert hardware_result is not None and "Ryzen" in hardware_result


@pytest.mark.asyncio
async def test_graph_client_cache(isolated_graph_client):
    res1 = await isolated_graph_client.search("budget")
    assert len(isolated_graph_client._query_cache) > 0
    res2 = await isolated_graph_client.search("budget")
    assert res1 == res2


@pytest.mark.asyncio
async def test_graph_client_search_does_not_block_event_loop():
    """search() must offload embedding/scoring work to a thread so other
    coroutines (e.g. a concurrent /api/command HTTP request) keep running
    while a graph query for an active turn is in flight.

    Regression test: GraphRAGClient.search() used to call _query_graph()
    directly inside the async def, which ran the embedding model
    synchronously on the event loop thread and starved every other
    pending coroutine — including a scheduled sleep() callback — for the
    full duration of the call.
    """
    client = GraphRAGClient()
    await client.initialize()

    # Stand in for the real (fast, model-dependent) scoring work with a
    # deterministic slow synchronous call, so the test doesn't depend on
    # actual embedding-model latency to prove the blocking behavior.
    import time

    def slow_query_graph(query, domain=None):
        time.sleep(0.3)
        return "stubbed result"

    client.retrieval.query = slow_query_graph

    heartbeat_latency = None
    loop = asyncio.get_running_loop()
    # Captured BEFORE either task is created — if the loop is blocked, the
    # heartbeat coroutine cannot even start its own internal timer until
    # the blocking call releases the thread, so this outer measurement is
    # what actually catches that scheduling delay.
    overall_start = loop.time()

    async def heartbeat():
        nonlocal heartbeat_latency
        await asyncio.sleep(0.05)
        heartbeat_latency = loop.time() - overall_start

    search_task = asyncio.create_task(client.search("a brand new query about kayaking"))
    heartbeat_task = asyncio.create_task(heartbeat())

    await asyncio.gather(search_task, heartbeat_task)

    # A free event loop resolves the 0.05s sleep on schedule (~0.05s total
    # elapsed). If graph search blocks the loop, heartbeat can't even begin
    # its sleep until the 0.3s synchronous call fully completes, so the
    # total elapsed time balloons toward 0.35s.
    assert heartbeat_latency < 0.2, (
        f"heartbeat took {heartbeat_latency:.3f}s end-to-end — "
        "graph search appears to be blocking the event loop"
    )


# ---------------------------------------------------------------------------
# graphrag_injector_node — dual-source parallel injection
# ---------------------------------------------------------------------------

def _make_mock_node(graph_result, activity_result=None, session_result=None):
    """Return a graphrag_injector_node backed by mocked clients."""
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value=graph_result)

    activity_client = None
    if activity_result is not None:
        activity_client = MagicMock()
        activity_client.search = AsyncMock(return_value=activity_result)

    session_client = None
    if session_result is not None:
        session_client = MagicMock()
        session_client.search = AsyncMock(return_value=session_result)

    return make_graphrag_node(graph_client, activity_client, None, session_client)


# The node returns personal knowledge in a SEPARATE ``knowledge_boost`` field
# (NOT appended to system_instruction) so the system prompt stays byte-stable for
# local-model KV-cache reuse. The supervisor folds this into the trailing user turn.


@pytest.mark.asyncio
async def test_node_injects_graph_context():
    node = _make_mock_node(graph_result="- The user is 30 years old.")
    state = {"messages": [HumanMessage(content="How old am I?")], "system_instruction": "base"}
    result = await node(state, config=None)
    assert "RELATIONAL CONTEXT (GRAPH)" in result["knowledge_boost"]
    assert "user is 30" in result["knowledge_boost"]
    # System prompt must NOT be mutated — that would break prefix caching.
    assert "system_instruction" not in result


@pytest.mark.asyncio
async def test_node_injects_activity_context():
    node = _make_mock_node(graph_result=None, activity_result="- Watched YouTube: Chess openings")
    # Query must be long enough to bypass is_conversational's short-message heuristic
    state = {"messages": [HumanMessage(content="what was that chess opening video I was watching?")], "system_instruction": "base"}
    result = await node(state, config=None)
    assert "ACTIVITY CONTEXT" in result["knowledge_boost"]
    assert "Chess openings" in result["knowledge_boost"]


@pytest.mark.asyncio
async def test_node_injects_both_blocks():
    node = _make_mock_node(
        graph_result="- The user works at Acme Corp.",
        activity_result="- Visited ProjectAlpha competitor analysis doc.",
    )
    state = {"messages": [HumanMessage(content="what's the plan for ProjectAlpha?")], "system_instruction": "base"}
    result = await node(state, config=None)
    boost = result["knowledge_boost"]
    assert "RELATIONAL CONTEXT (GRAPH)" in boost
    assert "ACTIVITY CONTEXT" in boost


@pytest.mark.asyncio
async def test_node_skips_injection_for_voice():
    node = _make_mock_node(graph_result="- some fact")
    state = {"messages": [HumanMessage(content="hey")], "system_instruction": "base", "category": "voice"}
    result = await node(state, config=None)
    assert result.get("knowledge_boost", "") == ""


@pytest.mark.asyncio
async def test_node_no_boost_when_both_empty():
    node = _make_mock_node(graph_result=None, activity_result=None)
    state = {"messages": [HumanMessage(content="what is the capital of France?")], "system_instruction": "base"}
    result = await node(state, config=None)
    assert result.get("knowledge_boost", "") == ""


@pytest.mark.asyncio
async def test_node_tolerates_activity_exception():
    """Activity search failure should not crash the node — graph result still injects."""
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- The user is 30.")

    activity_client = MagicMock()
    activity_client.search = AsyncMock(side_effect=RuntimeError("Supabase down"))

    node = make_graphrag_node(graph_client, activity_client)
    state = {"messages": [HumanMessage(content="How old am I?")], "system_instruction": "base"}
    result = await node(state, config=None)
    assert "RELATIONAL CONTEXT (GRAPH)" in result["knowledge_boost"]
    assert "ACTIVITY CONTEXT" not in result["knowledge_boost"]


@pytest.mark.asyncio
async def test_node_injects_session_history_context():
    node = _make_mock_node(
        graph_result=None,
        session_result="- [Session 2026-07-20] User: search anime website\nChad: found a few options",
    )
    state = {
        "messages": [HumanMessage(content="what anime sites did we talk about?")],
        "system_instruction": "base",
    }
    result = await node(state, config=None)
    assert "SESSION HISTORY" in result["knowledge_boost"]
    assert "search anime website" in result["knowledge_boost"]


@pytest.mark.asyncio
async def test_node_tolerates_session_history_exception():
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- The user is 30.")
    session_client = MagicMock()
    session_client.search = AsyncMock(side_effect=RuntimeError("checkpoint db locked"))

    node = make_graphrag_node(graph_client, None, None, session_client)
    state = {"messages": [HumanMessage(content="How old am I?")], "system_instruction": "base"}
    result = await node(state, config=None)
    assert "RELATIONAL CONTEXT (GRAPH)" in result["knowledge_boost"]
    assert "SESSION HISTORY" not in result["knowledge_boost"]


# ---------------------------------------------------------------------------
# fetch_knowledge_boost + pre-seeded injector short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_knowledge_boost_formats_graph_and_activity():
    from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost

    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- User is tracking a stipend increase.")
    activity_client = MagicMock()
    activity_client.search = AsyncMock(
        return_value="- Email: 'Stipend Rate Increase Request' from Smart Scholar"
    )

    boost = await fetch_knowledge_boost(
        "next step for stipend increase",
        graph_client,
        activity_client=activity_client,
    )
    assert "RELATIONAL CONTEXT (GRAPH)" in boost
    assert "ACTIVITY CONTEXT" in boost
    assert "Stipend Rate Increase Request" in boost
    graph_client.search.assert_awaited_once()
    activity_client.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_knowledge_boost_tolerates_client_exception():
    from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost

    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- fact")
    activity_client = MagicMock()
    activity_client.search = AsyncMock(side_effect=RuntimeError("down"))

    boost = await fetch_knowledge_boost("query", graph_client, activity_client=activity_client)
    assert "RELATIONAL CONTEXT (GRAPH)" in boost
    assert "ACTIVITY CONTEXT" not in boost


@pytest.mark.asyncio
async def test_node_reuses_preseeded_knowledge_boost_without_searching():
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- should not be used")
    activity_client = MagicMock()
    activity_client.search = AsyncMock(return_value="- should not be used")

    node = make_graphrag_node(graph_client, activity_client)
    preseeded = (
        "\n\n### ACTIVITY CONTEXT (PERSONAL INDEX)\n"
        "- Email: 'Stipend Rate Increase Request' from Smart Scholar\n"
    )
    state = {
        "messages": [HumanMessage(content="find what the next step for stipend increase request is")],
        "system_instruction": "base",
        "knowledge_boost": preseeded,
    }
    result = await node(state, config=None)
    assert result["knowledge_boost"] == preseeded
    graph_client.search.assert_not_called()
    activity_client.search.assert_not_called()
