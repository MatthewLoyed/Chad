"""Unit tests for the pure write-path planning in graph_runtime_relations (#657/#656).

No graph, no I/O, no model — these exercise entity resolution and supersession
cardinality directly, which is where the judgment calls live. The end-to-end
behaviour through GraphRAGClient.ingest() is covered in test_memory_core_recall.
"""

from __future__ import annotations

import pytest

from src.core.intelligence.graph_runtime_relations import (
    PlannedEdge,
    plan_runtime_relations,
    plan_supersessions,
)

EGO = "Person:Alex"


def _entity(node_id: str, entity_type: str, name: str) -> tuple[str, dict]:
    return node_id, {"kind": "entity", "entity_type": entity_type, "name": name, "valid": True}


NODES = [
    _entity(EGO, "Person", "Alex"),
    _entity("Person:Sam", "Person", "Sam"),
    _entity("Org:HarborDynamics", "Org", "HarborDynamics"),
    _entity("Place:Portland", "Place", "Portland"),
]


def _live(source: str, target: str, relation: str) -> tuple[str, str, str, dict]:
    return source, target, relation, {"relation": relation, "valid_to": None}


EDGES = [
    _live(EGO, "Org:HarborDynamics", "employed_by"),
    _live(EGO, "Place:Portland", "lives_in"),
    _live("Person:Sam", EGO, "partner_of"),
    _live(EGO, "Org:HarborDynamics", "member_of"),
]


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,expected_id",
    [
        # CamelCase in the graph vs spaced in the sentence — the resolution that,
        # when it failed, produced a WRONG supersession (a live edge expired
        # because the same employer looked like a new one).
        ("Alex works at Harbor Dynamics.", "Org:HarborDynamics"),
        ("Alex works at HarborDynamics.", "Org:HarborDynamics"),
        # A longer name containing the known one is the same entity.
        ("Sam Okafor is my partner.", "Person:Sam"),
        # A genuinely different employer gets a new id.
        ("Alex works at Redwing Marine.", "Org:Redwing Marine"),
    ],
)
def test_entity_resolution(content, expected_id):
    planned = plan_runtime_relations(content, NODES, EGO)
    assert [p.entity_id for p in planned] == [expected_id]


# ---------------------------------------------------------------------------
# Supersession cardinality
# ---------------------------------------------------------------------------


def test_new_value_of_single_valued_relation_supersedes():
    planned = plan_runtime_relations("Alex now works at Redwing Marine.", NODES, EGO)
    stale = plan_supersessions(planned, EDGES, EGO)
    assert [(s.relation, s.old_value_id) for s in stale] == [
        ("employed_by", "Org:HarborDynamics")
    ]


def test_same_value_restated_supersedes_nothing():
    planned = plan_runtime_relations("Alex works at Harbor Dynamics.", NODES, EGO)
    assert plan_supersessions(planned, EDGES, EGO) == []


def test_multi_valued_relation_supersedes_nothing():
    """member_of is deliberately NOT single-valued — a club must not end a job."""
    planned = [PlannedEdge("Org:Gym", "Org", "Gym", EGO, "Org:Gym", "member_of")]
    assert plan_supersessions(planned, EDGES, EGO) == []


def test_supersession_works_when_ego_is_the_edge_target():
    """partner_of points AT the ego, so cardinality can't assume edge direction."""
    planned = [PlannedEdge("Person:Jordan", "Person", "Jordan", "Person:Jordan", EGO, "partner_of")]
    stale = plan_supersessions(planned, EDGES, EGO)
    assert [(s.relation, s.old_value_id) for s in stale] == [("partner_of", "Person:Sam")]


def test_already_expired_edge_is_not_re_expired():
    """A repeated save must not churn valid_to on history."""
    expired = [(EGO, "Org:HarborDynamics", "employed_by",
                {"relation": "employed_by", "valid_to": "2026-01-01"})]
    planned = plan_runtime_relations("Alex now works at Redwing Marine.", NODES, EGO)
    assert plan_supersessions(planned, expired, EGO) == []


def test_edges_not_touching_ego_are_ignored():
    """Someone else's employer is not the user's, and must never be expired."""
    other = [("Person:Sam", "Org:Elsewhere", "employed_by",
              {"relation": "employed_by", "valid_to": None})]
    planned = plan_runtime_relations("Alex now works at Redwing Marine.", NODES, EGO)
    assert plan_supersessions(planned, other, EGO) == []
