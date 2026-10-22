"""Pure planning for GraphRAGClient._apply_runtime_relations() (#657).

Split out of graph_rag_client.py, which owns the graph itself and was already
at the module-size ceiling. No graph mutation and no I/O here -- given a fact's
text and the graph's current nodes, this decides which entities/edges a
runtime save should attach, leaving the actual `_ensure_entity` /
`upsert_inferred_edge` calls (and their persistence) to the caller.

Entity resolution matters here: "Sam" and "Sam Okafor" must not become two
`Person:` nodes. An exact id match wins outright; otherwise, if one name's
tokens are a subset of the other's, they're the same entity under a
shorter/longer name -- reuse whichever existing node has more tokens already
known, so a fuller name doesn't get discarded in favor of a first-seen partial
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from src.core.intelligence.personal_knowledge_parse import (
    SINGLE_VALUED_RELATIONS,
    entity_node_id,
    extract_runtime_relations,
    name_tokens,
)


@dataclass
class PlannedEdge:
    entity_id: str
    entity_type: str
    name: str
    source_id: str
    target_id: str
    relation: str


@dataclass
class SupersededEdge:
    """A live edge that a new single-valued assertion replaces (#656)."""

    source_id: str
    target_id: str
    relation: str
    # The end of the edge that is NOT the user -- what the old value actually
    # was ("Harbor Dynamics"), which is what the model needs told back to it.
    old_value_id: str


def _resolve_entity_id(nodes: list[tuple[str, dict]], entity_type: str, name: str) -> str:
    exact = entity_node_id(entity_type, name)
    if any(nid == exact for nid, _ in nodes):
        return exact

    wanted = name_tokens(name)
    if not wanted:
        return exact

    best: Optional[str] = None
    best_tokens = -1
    for node_id, data in nodes:
        if data.get("kind") != "entity" or data.get("entity_type") != entity_type:
            continue
        if not data.get("valid", True):
            continue
        existing = name_tokens(data.get("name") or "")
        if not existing:
            continue
        if wanted <= existing or existing <= wanted:
            if len(existing) > best_tokens:
                best, best_tokens = str(node_id), len(existing)

    return best or exact


def plan_runtime_relations(
    content: str, nodes: Iterable[tuple[str, dict]], ego_id: str
) -> list[PlannedEdge]:
    """Resolve every deterministic relation in `content` against the current graph."""
    node_list = list(nodes)
    planned: list[PlannedEdge] = []
    for rel in extract_runtime_relations(content):
        entity_id = _resolve_entity_id(node_list, rel.entity_type, rel.name)
        source_id, target_id = (
            (ego_id, entity_id) if rel.source_is_ego else (entity_id, ego_id)
        )
        planned.append(
            PlannedEdge(entity_id, rel.entity_type, rel.name, source_id, target_id, rel.relation)
        )
    return planned


def plan_supersessions(
    planned: Iterable[PlannedEdge],
    edges: Iterable[tuple[str, str, str, dict]],
    ego_id: str,
) -> list[SupersededEdge]:
    """Which live edges does `planned` replace outright?

    A single-valued relation (see SINGLE_VALUED_RELATIONS) admits one live edge
    per user, so asserting a new value expires the old one instead of racing it
    at retrieval time. Cardinality is anchored on the *ego* end regardless of
    edge direction -- `ego -employed_by-> Org` and `Person -partner_of-> ego`
    are both "the user has one of these" -- because every runtime-extracted
    relation is about the user.

    `edges` is `(source, target, key, data)` as yielded by
    `MultiDiGraph.edges(keys=True, data=True)`. Re-asserting the SAME value is
    not a supersession: it must not expire the edge it is about to re-add.
    Already-expired edges are skipped, so a repeated save can't churn
    `valid_to` on history.
    """
    edge_list = list(edges)
    out: list[SupersededEdge] = []

    for plan in planned:
        if plan.relation not in SINGLE_VALUED_RELATIONS:
            continue
        for source, target, key, data in edge_list:
            if data.get("relation", key) != plan.relation:
                continue
            if data.get("valid_to"):
                continue
            if ego_id not in (source, target):
                continue
            # The same assertion again -- refresh, don't expire-and-readd.
            if {source, target} == {plan.source_id, plan.target_id}:
                continue
            out.append(
                SupersededEdge(
                    source_id=source,
                    target_id=target,
                    relation=plan.relation,
                    old_value_id=target if source == ego_id else source,
                )
            )
    return out
