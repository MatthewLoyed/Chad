"""Unit tests for personal knowledge ## _graph parse + GraphRAG schema migration (#608)."""

from __future__ import annotations

import json

import networkx as nx
import pytest

from src.core.intelligence import graph_rag_client as graph_rag_client_module
from src.core.intelligence.personal_knowledge_parse import (
    parse_graph_block,
    parse_personal_knowledge,
)


SAMPLE_MD = """# Personal knowledge

## identity

Alex Example lives in Austin.

## family

Sam is Alex's partner.

## _graph

- Person:Alex
- Person:Sam
- Sam -partner_of-> Alex
- Event:Wedding2026 -happened_at-> Place:Venue
- Document:budget.xlsx -about-> Event:Wedding2026
- Document:budget.xlsx -in_service_of-> Goal:wedding_planning
"""


def _patch_seeds(monkeypatch, facts, entities, edges):
    """initialize() reloads via _load_personal_knowledge_seed — patch that too."""
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: (facts, entities, edges),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", facts)
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", entities)
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", edges)


def test_parse_skips_graph_section_as_fact():
    parsed = parse_personal_knowledge(SAMPLE_MD)
    topics = {f["id"] for f in parsed.facts}
    assert topics == {"identity", "family"}
    assert "_graph" not in topics


def test_parse_graph_entities_and_typed_edges():
    parsed = parse_personal_knowledge(SAMPLE_MD)
    ids = {e.id for e in parsed.entities}
    assert "Person:Alex" in ids
    assert "Person:Sam" in ids
    assert "Event:Wedding2026" in ids
    assert "Place:Venue" in ids
    assert "Document:budget.xlsx" in ids
    assert "Goal:wedding_planning" in ids

    rels = {(e.source_id, e.relation, e.target_id) for e in parsed.edges}
    assert ("Person:Sam", "partner_of", "Person:Alex") in rels
    assert ("Event:Wedding2026", "happened_at", "Place:Venue") in rels
    assert ("Document:budget.xlsx", "about", "Event:Wedding2026") in rels
    assert ("Document:budget.xlsx", "in_service_of", "Goal:wedding_planning") in rels


# ---------------------------------------------------------------------------
# Edge validity clause (#656)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected_from,expected_to",
    [
        ("- Alex -employed_by-> Org:OldCo (since 2021-04, until 2024-09)", "2021-04", "2024-09"),
        ("- Alex -employed_by-> Org:OldCo (until 2024-09)", None, "2024-09"),
        ("- Alex -employed_by-> Org:NewCo (since 2024-10)", "2024-10", None),
        ("- Alex -employed_by-> Org:NewCo", None, None),
    ],
)
def test_edge_validity_clause_parses(line, expected_from, expected_to):
    parsed = parse_personal_knowledge(f"## _graph\n\n- Person:Alex\n{line}\n")
    assert len(parsed.edges) == 1, f"expected exactly one edge from {line!r}"
    edge = parsed.edges[0]
    assert edge.valid_from == expected_from
    assert edge.valid_to == expected_to
    # The clause must not bleed into the target id.
    assert "(" not in edge.target_id


def test_edge_without_validity_is_byte_identical_to_before():
    """Backwards compatibility: existing ## _graph blocks parse exactly as they did."""
    parsed = parse_personal_knowledge(SAMPLE_MD)
    assert all(e.valid_from is None and e.valid_to is None for e in parsed.edges)


# ---------------------------------------------------------------------------
# Entity attribute clause (#659)
# ---------------------------------------------------------------------------


def test_entity_attributes_parse():
    parsed = parse_personal_knowledge(
        "## _graph\n\n- Org:Northgate (in Corvallis, Oregon)\n- Person:Alex\n"
    )
    by_id = {e.id: e for e in parsed.entities}
    assert by_id["Org:Northgate"].attributes == "in Corvallis, Oregon"
    assert by_id["Org:Northgate"].name == "Northgate", "clause must not bleed into the name"
    assert by_id["Person:Alex"].attributes is None


def test_entity_attributes_survive_declaration_after_an_edge():
    """An edge line creates the entity first; its later declaration still lands."""
    parsed = parse_personal_knowledge(
        "## _graph\n\n"
        "- Person:Alex\n"
        "- Alex -attended-> Org:Northgate\n"
        "- Org:Northgate (in Corvallis, Oregon)\n"
    )
    by_id = {e.id: e for e in parsed.entities}
    assert by_id["Org:Northgate"].attributes == "in Corvallis, Oregon"


def test_entities_without_attributes_are_unchanged():
    """Backwards compatibility: no attribute clause means no attributes, as before."""
    parsed = parse_personal_knowledge(SAMPLE_MD)
    assert all(e.attributes is None for e in parsed.entities)


def test_parse_bare_names_resolve_to_declared_entities():
    entities, edges = parse_graph_block(
        "Person:Sam\nPerson:Alex\nSam -partner_of-> Alex\n"
    )
    assert {e.id for e in entities} == {"Person:Sam", "Person:Alex"}
    assert len(edges) == 1
    assert edges[0].source_id == "Person:Sam"
    assert edges[0].target_id == "Person:Alex"
    assert edges[0].relation == "partner_of"


def test_parse_fully_qualified_edge_without_prior_declaration():
    entities, edges = parse_graph_block(
        "Person:Sam -partner_of-> Person:Alex\n"
    )
    assert {e.id for e in entities} == {"Person:Sam", "Person:Alex"}
    assert edges[0].relation == "partner_of"


@pytest.mark.asyncio
async def test_seed_includes_curated_graph_edges(tmp_path, monkeypatch):
    parsed = parse_personal_knowledge(SAMPLE_MD)
    _patch_seeds(monkeypatch, parsed.facts, parsed.entities, parsed.edges)

    from src.shared.config_manager import ConfigManager

    graph_path = str(tmp_path / "kg.json")
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()

    assert client._graph.nodes["Person:Sam"]["kind"] == "entity"
    assert client._graph.nodes["identity"]["kind"] == "fact"
    edge = client._graph.edges["Person:Sam", "Person:Alex", "partner_of"]
    assert edge["relation"] == "partner_of"
    assert edge["confidence"] == "curated"

    status = client.get_status()
    assert status["total_facts"] == 2
    assert status["total_entities"] >= 2

    rels = client.list_relationships(limit=50)
    texts = [r["content"] for r in rels]
    assert any("partner_of" in t for t in texts)


@pytest.mark.asyncio
async def test_walk_alex_sam_fiancee_query(tmp_path, monkeypatch):
    md = """## identity

Alex is the user.

## _graph

- Person:Alex
- Person:Sam
- Sam -partner_of-> Alex
"""
    parsed = parse_personal_knowledge(md)
    _patch_seeds(monkeypatch, parsed.facts, parsed.entities, parsed.edges)

    from src.shared.config_manager import ConfigManager

    graph_path = str(tmp_path / "kg_fiancee.json")
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))
    monkeypatch.setattr(
        graph_rag_client_module.GraphRAGClient,
        "_get_embedding",
        lambda self, text: None,
    )

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()

    result = await client.search("Who is Alex's fiancée?")
    assert result is not None
    assert "Sam" in result
    assert "partner_of" in result
    assert "Alex" in result


@pytest.mark.asyncio
async def test_boost_respects_max_boost_chars(tmp_path, monkeypatch):
    edges = "\n".join(
        f"- Person:P{i} -friend_of-> Person:P{i+1}" for i in range(20)
    )
    md = "## _graph\n" + "\n".join(f"- Person:P{i}" for i in range(21)) + "\n" + edges
    parsed = parse_personal_knowledge(md)
    _patch_seeds(monkeypatch, [], parsed.entities, parsed.edges)

    from src.shared.config_manager import ConfigManager

    graph_path = str(tmp_path / "kg_budget.json")
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        if key_path == "knowledge_graph.max_results":
            return 50
        if key_path == "knowledge_graph.max_boost_chars":
            return 120
        if key_path == "knowledge_graph.walk_hops":
            return 5
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))
    monkeypatch.setattr(
        graph_rag_client_module.GraphRAGClient,
        "_get_embedding",
        lambda self, text: None,
    )

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()
    result = await client.search("P0 friends")
    assert result is not None
    assert len(result) <= 120


@pytest.mark.asyncio
async def test_forget_entity_hides_walk_edge(tmp_path, monkeypatch):
    md = """## _graph

- Person:Alex
- Person:Sam
- Sam -partner_of-> Alex
"""
    parsed = parse_personal_knowledge(md)
    _patch_seeds(monkeypatch, [], parsed.entities, parsed.edges)

    from src.shared.config_manager import ConfigManager

    graph_path = str(tmp_path / "kg_forget.json")
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))
    monkeypatch.setattr(
        graph_rag_client_module.GraphRAGClient,
        "_get_embedding",
        lambda self, text: None,
    )

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()
    assert "partner_of" in (await client.search("Alex partner") or "")

    outcome = await client.forget("Sam")
    assert outcome["invalidated"]
    after = await client.search("Alex partner")
    assert after is None or "Sam" not in after


async def _client_with_facts(tmp_path, monkeypatch, facts, entities=None, edges=None):
    """Build an initialized GraphRAGClient seeded with the given fact/entity fixtures."""
    _patch_seeds(monkeypatch, facts, entities or [], edges or [])

    from src.shared.config_manager import ConfigManager

    graph_path = str(tmp_path / "kg_forget_sledgehammer.json")
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))
    monkeypatch.setattr(
        graph_rag_client_module.GraphRAGClient,
        "_get_embedding",
        lambda self, text: None,
    )

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()
    return client


@pytest.mark.asyncio
async def test_forget_specific_description_invalidates_exactly_that_fact(tmp_path, monkeypatch):
    """#658: a specific description should invalidate the one fact it names."""
    client = await _client_with_facts(
        tmp_path,
        monkeypatch,
        [
            {"id": "f1", "topic": "identity", "content": "Alex's email is alex.rivera@example.com."},
            {"id": "f2", "topic": "career", "content": "Alex works at Harbor Dynamics."},
        ],
    )

    outcome = await client.forget("Alex's email is alex.rivera@example.com.")
    assert [item["content"] for item in outcome["invalidated"]] == [
        "Alex's email is alex.rivera@example.com."
    ]
    assert outcome["candidates"] == []
    assert outcome["ambiguous"] is False

    after = await client.search("Alex email")
    assert after is None or "alex.rivera@example.com" not in after
    still_there = await client.search("Harbor Dynamics")
    assert still_there is not None and "Harbor Dynamics" in still_there


@pytest.mark.asyncio
async def test_forget_vague_description_invalidates_nothing(tmp_path, monkeypatch):
    """#658: a short/vague description must not mass-invalidate -- the regression that matters."""
    client = await _client_with_facts(
        tmp_path,
        monkeypatch,
        [
            {"id": "f1", "topic": "identity", "content": "Alex's email is alex.rivera@example.com."},
            {"id": "f2", "topic": "career", "content": "Alex works at Harbor Dynamics."},
            {"id": "f3", "topic": "family", "content": "Sam's email is sam.okafor@example.com."},
        ],
    )

    outcome = await client.forget("my email")

    assert outcome["invalidated"] == []
    assert outcome["ambiguous"] is True
    assert len(outcome["candidates"]) >= 2
    still_there = await client.search("Alex email")
    assert still_there is not None and "alex.rivera@example.com" in still_there


@pytest.mark.asyncio
async def test_forget_never_collaterally_invalidates_entity(tmp_path, monkeypatch):
    """#658: forgetting a fact about a person must not also invalidate their entity node."""
    md = """## _graph

- Person:Alex
- Person:Sam
- Sam -partner_of-> Alex
"""
    parsed = parse_personal_knowledge(md)
    client = await _client_with_facts(
        tmp_path,
        monkeypatch,
        [{"id": "f1", "topic": "career", "content": "Alex works at Harbor Dynamics as a design engineer."}],
        entities=parsed.entities,
        edges=parsed.edges,
    )

    outcome = await client.forget("Alex no longer works at Harbor Dynamics")

    assert [item["content"] for item in outcome["invalidated"]] == [
        "Alex works at Harbor Dynamics as a design engineer."
    ]
    entity_node = client._graph.nodes["Person:Alex"]
    assert entity_node.get("valid", True) is True


@pytest.mark.asyncio
async def test_runtime_relation_extraction_noop_without_ego(tmp_path, monkeypatch):
    """#657: a graph with no Person entity yet must not crash or fabricate one on save."""
    client = await _client_with_facts(tmp_path, monkeypatch, [])

    await client.ingest("Sam is my partner.", source="manual")

    assert not any(
        data.get("kind") == "entity" for _, data in client._graph.nodes(data=True)
    )


@pytest.mark.asyncio
async def test_legacy_json_migration_preserves_nodes(tmp_path, monkeypatch):
    """Pre-#608 graphs lack kind/confidence — load must backfill without data loss."""
    _patch_seeds(monkeypatch, [], [], [])

    from src.shared.config_manager import ConfigManager

    graph_path = tmp_path / "legacy.json"
    g = nx.DiGraph()
    g.add_node(
        "hardware_specs",
        content="AMD Ryzen desktop",
        topic="hardware",
        source="seed",
        created_at="2026-01-01T00:00:00+00:00",
        valid=True,
    )
    g.add_node(
        "budget_overview",
        content="Wedding budget tracking",
        topic="budget",
        source="seed",
        created_at="2026-01-01T00:00:00+00:00",
        valid=True,
    )
    g.add_edge("hardware_specs", "budget_overview", relation="related")
    graph_path.write_text(json.dumps(nx.node_link_data(g)), encoding="utf-8")

    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return str(graph_path)
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))

    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()

    assert client._graph.number_of_nodes() == 2
    assert client._graph.nodes["hardware_specs"]["kind"] == "fact"
    assert client._graph.nodes["budget_overview"]["kind"] == "fact"
    edge = client._graph.edges["hardware_specs", "budget_overview", "related"]
    assert edge["relation"] == "related"
    assert edge["confidence"] == "inferred"
    assert "valid_from" in edge
    assert "valid_to" in edge

    # Persisted migration
    saved = json.loads(graph_path.read_text(encoding="utf-8"))
    node_by_id = {n["id"]: n for n in saved["nodes"]}
    assert node_by_id["hardware_specs"]["kind"] == "fact"
