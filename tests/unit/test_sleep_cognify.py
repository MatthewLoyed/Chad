"""Unit tests for sleep cognify (#611) — Tier-1 gating, propose/apply, graph additions."""

from __future__ import annotations

import pytest

from src.core.intelligence import graph_rag_client as graph_rag_client_module
from src.core.intelligence import sleep_cognify


def _patch_graph_path(monkeypatch, tmp_path, filename="kg.json"):
    from src.shared.config_manager import ConfigManager

    graph_path = str(tmp_path / filename)
    real_get = ConfigManager.get.__func__

    def fake_get(cls, key_path, default=None):
        if key_path == "knowledge_graph.graph_path":
            return graph_path
        return real_get(cls, key_path, default)

    monkeypatch.setattr(ConfigManager, "get", classmethod(fake_get))
    monkeypatch.setattr(graph_rag_client_module, "_load_personal_knowledge_seed", lambda: ([], [], []))
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", [])
    monkeypatch.setattr(graph_rag_client_module.GraphRAGClient, "_get_embedding", lambda self, text: None)


# ──────────────────────────────────────────────────────────────────────────────
# Tier-1 sensitivity classifier — deterministic, no LLM
# ──────────────────────────────────────────────────────────────────────────────

def test_classify_tier_health():
    assert sleep_cognify.classify_tier("Started a new medication for the flare") == "health"


def test_classify_tier_financial():
    assert sleep_cognify.classify_tier("Took out a loan against savings") == "financial"


def test_classify_tier_none_for_ordinary_fact():
    assert sleep_cognify.classify_tier("WeddingBudget -about-> Wedding2026") is None


# ──────────────────────────────────────────────────────────────────────────────
# propose_cognify — deterministic gate over a canned judge result
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_propose_cognify_routes_tier1_and_nonsensitive(monkeypatch):
    canned = sleep_cognify.CognifyResult(
        fact_count=3,
        model="test-model",
        items=[
            {"kind": "edge", "source": "Document:Budget", "relation": "about",
             "target": "Event:Wedding", "evidence": ["fact_001"], "confidence": 0.9},
            {"kind": "edge", "source": "Person:Alex", "relation": "in_service_of",
             "target": "Goal:therapy", "evidence": ["fact_002"], "confidence": 0.9},
            {"kind": "observation", "text": "Alex is consistently working toward the May wedding.",
             "evidence": ["fact_001", "fact_003"], "confidence": 0.8},
            {"kind": "core_profile", "text": "Alex, engaged to Sam, CTO of Tidepool.",
             "confidence": 0.7},
        ],
    )

    async def fake_cognify(model, *, core_profile_max_chars=800):
        return canned

    monkeypatch.setattr(sleep_cognify, "cognify", fake_cognify)

    proposal = await sleep_cognify.propose_cognify("test-model", confidence_floor=0.6)

    assert len(proposal.auto_admit_edges) == 1
    assert proposal.auto_admit_edges[0]["target"] == "Event:Wedding"

    # The "therapy" edge is Tier-1 (health keyword in the target name) — queued, not auto-admitted.
    assert len(proposal.pending_review) == 1
    assert proposal.pending_review[0]["tier"] == "health"

    assert len(proposal.auto_admit_observations) == 1
    assert proposal.core_profile_candidate is not None
    assert proposal.core_profile_candidate["text"].startswith("Alex")


@pytest.mark.asyncio
async def test_propose_cognify_drops_low_confidence(monkeypatch):
    canned = sleep_cognify.CognifyResult(
        fact_count=1, model="test-model",
        items=[
            {"kind": "edge", "source": "Person:A", "relation": "friend_of",
             "target": "Person:B", "evidence": ["fact_001"], "confidence": 0.2},
        ],
    )

    async def fake_cognify(model, *, core_profile_max_chars=800):
        return canned

    monkeypatch.setattr(sleep_cognify, "cognify", fake_cognify)
    proposal = await sleep_cognify.propose_cognify("test-model", confidence_floor=0.6)
    assert not proposal.auto_admit_edges
    assert not proposal.pending_review


# ──────────────────────────────────────────────────────────────────────────────
# GraphRAGClient additions — observation nodes, inferred edges, core profile
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_observation_is_retrievable(tmp_path, monkeypatch):
    _patch_graph_path(monkeypatch, tmp_path, "obs.json")
    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()

    node_id = await client.add_observation(
        "Alex is steadily planning the May 2027 wedding.", ["fact_a", "fact_b"])
    assert node_id in client._graph
    assert client._graph.nodes[node_id]["kind"] == "observation"
    assert client._graph.nodes[node_id]["evidence"] == ["fact_a", "fact_b"]

    result = await client.search("wedding planning")
    assert result is not None
    assert "May 2027" in result

    facts = client.list_active_facts(limit=10)
    assert any(f["id"] == node_id for f in facts)


@pytest.mark.asyncio
async def test_upsert_inferred_edge_creates_missing_entities(tmp_path, monkeypatch):
    _patch_graph_path(monkeypatch, tmp_path, "edge.json")
    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()

    ok = await client.upsert_inferred_edge("Person:Brand", "Project:NewThing", "owns")
    assert ok
    assert client._graph.nodes["Person:Brand"]["confidence"] == "inferred"
    edge = client._graph.edges["Person:Brand", "Project:NewThing", "owns"]
    assert edge["confidence"] == "inferred"


@pytest.mark.asyncio
async def test_upsert_inferred_edge_never_overwrites_curated(tmp_path, monkeypatch):
    _patch_graph_path(monkeypatch, tmp_path, "curated.json")
    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()
    client._graph.add_node("Person:A", kind="entity", name="A", valid=True)
    client._graph.add_node("Person:B", kind="entity", name="B", valid=True)
    client._graph.add_edge("Person:A", "Person:B", key="partner_of",
                           relation="partner_of", confidence="curated",
                           valid_from=None, valid_to=None)

    ok = await client.upsert_inferred_edge("Person:A", "Person:B", "partner_of", confidence="inferred")
    assert ok is False
    edge = client._graph.edges["Person:A", "Person:B", "partner_of"]
    assert edge["confidence"] == "curated"


@pytest.mark.asyncio
async def test_core_profile_round_trip_and_char_cap(tmp_path, monkeypatch):
    _patch_graph_path(monkeypatch, tmp_path, "profile.json")
    client = graph_rag_client_module.GraphRAGClient()
    await client.initialize()

    assert await client.get_core_profile() is None

    long_text = "x" * 2000
    saved = await client.set_core_profile(long_text, max_chars=100)
    assert len(saved) == 100
    assert await client.get_core_profile() == saved


# ──────────────────────────────────────────────────────────────────────────────
# apply_item — review-queue dispatch (mirrors what apply_lesson delegates to)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_item_writes_core_profile(tmp_path, monkeypatch):
    _patch_graph_path(monkeypatch, tmp_path, "apply_profile.json")
    ok = await sleep_cognify.apply_item({
        "scope": "core_profile",
        "payload": {"text": "Alex, engaged to Sam."},
    })
    assert ok

    from src.core.intelligence.graph_rag_client import GraphRAGClient
    client = GraphRAGClient()
    await client.initialize()
    assert await client.get_core_profile() == "Alex, engaged to Sam."


@pytest.mark.asyncio
async def test_apply_item_parses_json_evidence_column(tmp_path, monkeypatch):
    """DB rows carry the payload JSON-encoded into `evidence` (see persist_pending_items)."""
    _patch_graph_path(monkeypatch, tmp_path, "apply_row.json")
    row = {
        "scope": "observation",
        "text": "distilled belief",
        "evidence": '{"text": "distilled belief", "evidence": ["fact_1"]}',
    }
    ok = await sleep_cognify.apply_item(row)
    assert ok
