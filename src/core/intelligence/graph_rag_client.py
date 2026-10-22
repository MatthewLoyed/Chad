"""
Personal knowledge graph for Chad — NetworkX + JSON, no external services.

HOW IT WORKS
------------
Personal facts live in config/personal_knowledge.md — plain markdown,
one ## section per topic. On startup, GraphRAGClient reads that file and
seeds an empty graph and reconciles seed fact lines on every initialization.

An optional ``## _graph`` section declares typed entities and relations
(see docs/research/TYPED_GRAPH_MEMORY.md / #608). Those become entity
nodes and curated edges alongside fact blobs.

Facts added at runtime (via the manage_user_memory tool) are appended as
new nodes to the same graph and persisted immediately.

The graph file lives at data/knowledge_graph.json (path from config.yaml
knowledge_graph.graph_path). Routine markdown edits are reconciled on startup;
keep this file to preserve runtime facts and invalidation history.

TEMPORAL TRACKING
-----------------
Facts are never hard-deleted. `forget()` marks nodes invalid with a
timestamp so history is always recoverable.

QUERIED BY
----------
1. graphrag_injector_node in supervisor.py  — always-on (no keyword gate),
   runs in parallel with ActivityRAGClient on every non-conversational turn.
   Injects a RELATIONAL CONTEXT block into the LangGraph system_instruction.
2. MemoryEngine.get_unified_context()       — local TextMode path only.
   Injects as PERSONAL CONTEXT block via prompt_builder.py.

LAYOUT
------
This module OWNS the graph: load, seed, sync from markdown, write, forget,
persist, migrate. Three neighbours handle the other jobs:
  graph_retrieval.py         — query-time ranking/formatting (`client.retrieval`)
  graph_backup.py            — optional Supabase mirror of identity facts
  graph_forget.py            — pure scoring/decision logic for forget() (#658)
  graph_runtime_relations.py — pure entity-resolution/planning for runtime
                                saves joining the typed graph (#657), plus
                                single-valued supersession (#656)
  graph_always_on.py         — sync, mtime-cached reads of the core-profile and
                                behavior singletons (this module writes them)
Writes from the model go through memory_write_gate.py first.

DEBUG
-----
Call client.get_status() for a live node/edge/topic count.
Inspect data/knowledge_graph.json directly to see all nodes.
Set LOG_LEVEL=DEBUG to see every search scored.
"""

import asyncio
import json
import logging
import os
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import networkx as nx

from src.core.intelligence.graph_always_on import (
    BEHAVIOR_NODE_ID,
    CORE_PROFILE_NODE_ID,
    max_chars as always_on_max_chars,
    read_always_on,
)
from src.core.intelligence.graph_backup import mirror_facts_to_supabase
from src.core.orchestration.context import domain_var
from src.shared.domain_scope import normalize_domain, record_matches_domain
from src.core.intelligence.graph_forget import score_forget_candidates
from src.core.intelligence.graph_retrieval import (
    RETRIEVABLE_KINDS,
    GraphRetriever,
)
from src.core.intelligence.graph_runtime_relations import (
    plan_runtime_relations,
    plan_supersessions,
)
from src.core.intelligence.personal_knowledge_parse import (
    GraphEdge,
    GraphEntity,
    PersonalKnowledge,
    is_placeholder_text,
    parse_personal_knowledge,
)

logger = logging.getLogger("GraphRAGClient")

# ---------------------------------------------------------------------------
# Seed — loaded from config/personal_knowledge.md at import (tests may
# monkeypatch SEED_FACTS / SEED_ENTITIES / SEED_EDGES). Always reloaded from
# disk on initialize() so CHAD_HOME / edited personal_knowledge.md is picked up.
# ---------------------------------------------------------------------------
def _load_personal_knowledge_seed() -> PersonalKnowledge:
    """
    Load personal knowledge from config/personal_knowledge.md.
    Each ## section contributes one fact node per line; reserved sections supply
    typed entities/edges (`## _graph`), the always-on identity block
    (`## _core_profile`), and always-on behavior directives (`## _behavior`).
    Skips empty / template-placeholder sections so OSS scaffolds don't pollute search.
    """
    from src.shared.paths import get_project_root

    path = get_project_root() / "config" / "personal_knowledge.md"
    if not path.exists():
        logger.warning(f"[ GRAPH ] personal_knowledge.md not found at {path}. No seed facts loaded.")
        return PersonalKnowledge()

    parsed = parse_personal_knowledge(path.read_text(encoding="utf-8"))
    parsed.facts = [
        f for f in parsed.facts
        if f.get("content") and not is_placeholder_text(f.get("content") or "")
    ]
    if parsed.core_profile and is_placeholder_text(parsed.core_profile):
        parsed.core_profile = None
    if parsed.behavior and is_placeholder_text(parsed.behavior):
        parsed.behavior = None
    logger.info(
        f"[ GRAPH ] Loaded {len(parsed.facts)} seed facts, "
        f"{len(parsed.entities)} entities, {len(parsed.edges)} curated edges, "
        f"core_profile={'yes' if parsed.core_profile else 'no'}, "
        f"behavior={'yes' if parsed.behavior else 'no'} from personal_knowledge.md."
    )
    return parsed


def _coerce_seed(loaded) -> PersonalKnowledge:
    """Accept the legacy (facts, entities, edges[, core_profile]) tuple form.

    Tests and evals monkeypatch `_load_personal_knowledge_seed`; several still
    return the tuple this used to be.
    """
    if isinstance(loaded, PersonalKnowledge):
        return loaded
    items = list(loaded or [])
    return PersonalKnowledge(
        facts=items[0] if len(items) > 0 else [],
        entities=items[1] if len(items) > 1 else [],
        edges=items[2] if len(items) > 2 else [],
        core_profile=items[3] if len(items) > 3 else None,
    )


_SEED = _load_personal_knowledge_seed()
SEED_FACTS, SEED_ENTITIES, SEED_EDGES = _SEED.facts, _SEED.entities, _SEED.edges
SEED_CORE_PROFILE, SEED_BEHAVIOR = _SEED.core_profile, _SEED.behavior


class GraphRAGClient:
    """
    Personal knowledge graph backed by NetworkX and a local JSON file.
    No external services — starts up with Chad, persists between sessions.

    Interface is identical to the previous mock client so supervisor.py
    needs no changes.
    """

    def __init__(self, index_path: str = "./graphrag_data"):
        self._is_initialized = False
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._graph_path: str = ""
        self._query_cache: dict = {}
        self._embedding_model = None
        # Querying/ranking lives in GraphRetriever. The graph is passed as a getter
        # because `self._graph` is replaced wholesale on reload, and embedding as a
        # callable so monkeypatching `_get_embedding` in tests still reaches it.
        self.retrieval = GraphRetriever(
            graph_getter=lambda: self._graph,
            embed=lambda text: self._get_embedding(text),
        )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        if self._is_initialized:
            return True

        from src.shared.config_manager import ConfigManager
        from src.shared.paths import resolve_path

        enabled = ConfigManager.get("knowledge_graph.enabled", True)
        if not enabled:
            logger.info("[ GRAPH ] knowledge_graph.enabled=false — skipping.")
            self._is_initialized = True
            return True

        # Reload seeds every init (CHAD_HOME + edits to personal_knowledge.md).
        # Tests should monkeypatch `_load_personal_knowledge_seed`, not only SEED_*.
        global SEED_FACTS, SEED_ENTITIES, SEED_EDGES, SEED_CORE_PROFILE, SEED_BEHAVIOR
        seed = _coerce_seed(_load_personal_knowledge_seed())
        SEED_FACTS, SEED_ENTITIES, SEED_EDGES = seed.facts, seed.entities, seed.edges
        SEED_CORE_PROFILE, SEED_BEHAVIOR = seed.core_profile, seed.behavior

        graph_path = ConfigManager.get("knowledge_graph.graph_path", "data/knowledge_graph.json")
        self._graph_path = resolve_path(graph_path)
        os.makedirs(os.path.dirname(self._graph_path) or ".", exist_ok=True)

        self._load_graph()
        self._migrate_legacy_schema()

        if self._graph.number_of_nodes() == 0:
            self._seed_graph()

        self._sync_core_profile_from_markdown()
        self._sync_seed_facts_from_markdown()

        self._is_initialized = True
        valid_facts = sum(
            1
            for _, d in self._graph.nodes(data=True)
            if d.get("valid", True) and d.get("kind", "fact") == "fact"
        )
        valid_entities = sum(
            1
            for _, d in self._graph.nodes(data=True)
            if d.get("valid", True) and d.get("kind") == "entity"
        )
        valid_observations = sum(
            1
            for _, d in self._graph.nodes(data=True)
            if d.get("valid", True) and d.get("kind") == "observation"
        )
        edge_count = self._graph.number_of_edges()
        logger.warning(
            f"[ GRAPH ] ✓ Personal knowledge graph ready — "
            f"{valid_facts} facts, {valid_entities} entities, {valid_observations} observations, "
            f"{edge_count} relationships. File: {self._graph_path}"
        )
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(self, query: str, mode: str = "local",
                     domain: Optional[str] = None) -> Optional[str]:
        if not self._is_initialized:
            await self.initialize()

        # Falls back to the turn's scope so a caller that doesn't know about domains — e.g.
        # the manage_user_memory recall tool — is scoped by default rather than reading
        # across every domain. Callers that hop threads must still pass it explicitly.
        domain = normalize_domain(domain if domain is not None else domain_var.get())
        # The domain MUST be part of the cache key. Keyed on query text alone, asking the
        # same question in a general chat right after a Financial one would serve the cached
        # financial answer — isolation would appear to work and silently not.
        cache_key = (query.strip().lower(), mode, domain)
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self.retrieval.query, query, domain)

        if result:
            self._query_cache[cache_key] = result
        return result

    async def max_similarity(self, query: str,
                             domain: Optional[str] = None) -> tuple[float, Optional[str]]:
        """Return (best_score, best_content) for the valid node most similar to `query`.

        Unlike `search` (which returns a formatted, threshold-filtered bullet string),
        this exposes the raw top similarity so callers can decide their own threshold —
        e.g. the reflection loop uses it to detect a fact the graph ALREADY knows
        (cross-store dedup) before writing a lesson.
        """
        if not self._is_initialized:
            await self.initialize()
        # Scoped like search(): this returns fact CONTENT, so an unscoped call would hand a
        # financial fact to a general-tier caller doing cross-store dedup.
        domain = normalize_domain(domain if domain is not None else domain_var.get())
        valid_nodes = [
            (nid, data)
            for nid, data in self._graph.nodes(data=True)
            if data.get("valid", True)
            and data.get("kind", "fact") in RETRIEVABLE_KINDS
            and data.get("content")
            and not is_placeholder_text(data.get("content") or "")
            and record_matches_domain(data, domain)
        ]
        if not valid_nodes:
            return 0.0, None
        loop = asyncio.get_running_loop()
        scored = await loop.run_in_executor(None, self.retrieval.score_nodes, query, valid_nodes)
        if not scored:
            return 0.0, None
        _nid, data, score = max(scored, key=lambda x: x[2])
        return float(score), data.get("content")

    async def ingest(self, content: str, source: str = "manual", topic: str = "",
                     domain: Optional[str] = None) -> bool:
        """Add a new fact episode to the graph.

        `domain` scopes the fact to a domain agent and defaults to the active turn's scope
        (`domain_var`), so every caller that saves a fact mid-turn — manage_user_memory,
        reflection, learn_directive — is tagged without needing to know this feature exists.
        Pass it explicitly from work that runs OUTSIDE the turn's context (background
        consolidation), where the contextvar would read None and silently write a global fact.
        """
        if not self._is_initialized:
            await self.initialize()
        domain = normalize_domain(domain if domain is not None else domain_var.get())

        # Microseconds + a short suffix: second-resolution ids collided silently,
        # and add_node() on an existing id OVERWRITES, so two facts saved in the
        # same second left only the last one.
        now_utc = datetime.now(timezone.utc)
        node_id = f"{source}_{now_utc.strftime('%Y%m%d_%H%M%S_%f')}"
        if node_id in self._graph:
            node_id = f"{node_id}_{uuid.uuid4().hex[:6]}"
        detected_topic = topic or self._detect_topic(content)

        self._graph.add_node(
            node_id,
            kind="fact",
            content=content,
            topic=detected_topic,
            source=source,
            created_at=datetime.now(timezone.utc).isoformat(),
            valid=True,
            domain=domain,
        )

        # Link to existing nodes on the same topic
        self._link_related_nodes(node_id, detected_topic, content)

        # #657: attach entities/edges for the small set of deterministic
        # phrasings we recognize, so runtime saves join the typed walk instead
        # of being invisible to it (only reachable via embedding similarity).
        await self._apply_runtime_relations(content)

        self._save_graph()
        self._query_cache.clear()
        logger.info(f"[ GRAPH ] Added node '{node_id}' (topic: {detected_topic}).")
        return True

    async def batch_ingest(self, records: list, domain: Optional[str] = None) -> int:
        """
        Add multiple facts to the graph in one operation, saving only once at the end.
        Each record is a dict with 'content' (required), 'source', and 'topic' keys, and may
        carry a per-record 'domain' that overrides the batch-level `domain`.
        """
        if not self._is_initialized:
            await self.initialize()

        batch_domain = normalize_domain(domain if domain is not None else domain_var.get())
        added = 0
        for i, record in enumerate(records):
            content = record.get("content", "").strip()
            if not content:
                continue
            source = record.get("source", "migration")
            topic = record.get("topic", "")
            node_id = f"{source}_{i:05d}"
            detected_topic = topic or self._detect_topic(content)
            record_domain = normalize_domain(record.get("domain") or batch_domain)

            self._graph.add_node(
                node_id,
                kind="fact",
                content=content,
                topic=detected_topic,
                source=source,
                created_at=datetime.now(timezone.utc).isoformat(),
                valid=True,
                domain=record_domain,
            )
            added += 1

        if added:
            self._save_graph()
            self._query_cache.clear()
            logger.info(f"[ GRAPH ] Batch ingested {added} nodes.")

        return added

    async def forget(self, fact_description: str) -> dict:
        """Invalidate the single node that best matches `fact_description`.

        Preserves history (nodes are marked invalid, never deleted). See
        graph_forget.py for the scoring/decision logic -- only the best-scoring
        node is ever touched, and only when the match is decisive; otherwise
        nothing is invalidated and near-miss candidates are returned so the
        caller can be more specific.

        Returns:
            {"invalidated": [{"content": str, "score": float}, ...],
             "candidates": [{"content": str, "score": float}, ...],
             "ambiguous": bool}
            `invalidated` has at most one item. `candidates` is non-empty
            whenever something scored above zero but wasn't decisive enough to
            act on (a vague description, or a tie between two facts).
        """
        if not self._is_initialized:
            await self.initialize()

        decision = score_forget_candidates(fact_description, self._graph.nodes(data=True))

        if decision.invalidate is None:
            return {
                "invalidated": [],
                "candidates": [
                    {"content": c.content, "score": round(c.score, 2)} for c in decision.candidates
                ],
                "ambiguous": decision.ambiguous,
            }

        node_id = decision.invalidate.node_id
        self._graph.nodes[node_id]["valid"] = False
        self._graph.nodes[node_id]["invalidated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_graph()
        self._query_cache.clear()
        logger.info(f"[ GRAPH ] Invalidated node matching: '{fact_description[:50]}'.")

        return {
            "invalidated": [
                {"content": decision.invalidate.content, "score": round(decision.invalidate.score, 2)}
            ],
            "candidates": [],
            "ambiguous": False,
        }

    # ------------------------------------------------------------------
    # Sleep cognify (#611) — offline-only writes: observations, inferred
    # edges, and the char-capped core profile. Never called on the hot
    # (turn) path — see src/core/intelligence/sleep_cognify.py.
    # ------------------------------------------------------------------

    async def add_observation(
        self, content: str, evidence_ids: list[str], *, confidence: str = "observed",
        source: str = "sleep_cognify",
    ) -> str:
        """Add a sleep-consolidated observation node with evidence fact ids.

        Observations are short, durable beliefs distilled from one or more raw fact
        nodes (e.g. "Alex is planning toward a May 2028 wedding" from several
        wedding-topic facts). They participate in retrieval like facts but carry
        `evidence` (the fact/observation node ids they were distilled from) so the
        origin is always auditable and `forget()`-able.
        """
        if not self._is_initialized:
            await self.initialize()

        # Sleep-cognify runs offline, so domain_var is empty here — the observation inherits
        # its scope from the evidence it was distilled from instead. An observation is a
        # SUMMARY of its evidence, so writing it untagged would republish a scoped fact into
        # the global tier: the leak this feature exists to prevent.
        evidence_domains = {
            normalize_domain(self._graph.nodes.get(eid, {}).get("domain"))
            for eid in (evidence_ids or [])
        }
        scoped = {d for d in evidence_domains if d is not None}
        if len(scoped) > 1:
            # Distilled across two domains — there is no tag that isn't a leak in one
            # direction, so refuse the write rather than guess. Rare by construction:
            # sleep-cognify groups evidence by topic, and topics don't span domains.
            logger.warning(
                "[ GRAPH ] Refusing cross-domain observation (evidence spans %s): %.80s",
                sorted(scoped), content,
            )
            return ""
        observation_domain = next(iter(scoped), None)

        node_id = f"observation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
        self._graph.add_node(
            node_id,
            kind="observation",
            content=content,
            topic=self._detect_topic(content),
            source=source,
            confidence=confidence,
            evidence=list(evidence_ids or []),
            created_at=datetime.now(timezone.utc).isoformat(),
            valid=True,
            domain=observation_domain,
        )
        self._save_graph()
        self._query_cache.clear()
        logger.info(f"[ GRAPH ] Added observation '{node_id}' (evidence: {len(evidence_ids or [])} node(s)).")
        return node_id

    def _ensure_entity(self, node_id: str, *, entity_type: str = "Entity", name: str = "") -> None:
        """Create a minimal inferred entity node if `node_id` doesn't already exist.

        Lets sleep-cognify propose edges that reference an entity not yet declared in
        the curated `## _graph` block (e.g. a person mentioned only in prose) without
        clobbering a curated node that already exists at that id.
        """
        if node_id in self._graph:
            return
        if not name:
            name = node_id.split(":", 1)[1] if ":" in node_id else node_id
        self._graph.add_node(
            node_id,
            kind="entity",
            entity_type=entity_type,
            name=name,
            content=name,
            topic=entity_type.lower(),
            source="sleep_cognify",
            confidence="inferred",
            created_at=datetime.now(timezone.utc).isoformat(),
            valid=True,
        )

    async def upsert_inferred_edge(
        self, source_id: str, target_id: str, relation: str, *, confidence: str = "inferred",
    ) -> bool:
        """Add (or refresh) a sleep-cognify-proposed typed edge.

        Unlike `_upsert_curated_edge` (authored `## _graph`, always wins), this is for
        edges sleep cognify infers from facts/observations — e.g. `caused_by` /
        `in_service_of` links, or a `related_to_project` edge for a person mentioned
        alongside a project. Auto-creates missing endpoints as inferred entities
        (curated edges require both endpoints to already exist; inferred ones don't,
        since discovering new entities is the point of sleep cognify). A curated edge
        at the same (source, relation, target) key always wins — never overwritten.
        """
        if not self._is_initialized:
            await self.initialize()
        self._ensure_entity(source_id)
        self._ensure_entity(target_id)

        if self._graph.has_edge(source_id, target_id, key=relation):
            existing = self._graph.edges[source_id, target_id, relation]
            if existing.get("confidence") == "curated":
                return False  # curated always wins — never overwritten by inference
            existing.update({"relation": relation, "confidence": confidence})
        else:
            self._graph.add_edge(
                source_id, target_id, key=relation,
                relation=relation, confidence=confidence,
                valid_from=None, valid_to=None,
            )
        self._save_graph()
        self._query_cache.clear()
        return True

    async def _apply_runtime_relations(self, content: str) -> None:
        """Attach typed entities/edges for the deterministic phrasings in `content` (#657).

        No-op when the graph has no ego (Person) entity yet to anchor the edge to
        -- a brand-new install with nothing in ## _graph -- since every pattern
        recognized here is relative to the user. Broader extraction is
        sleep-cognify's job, off the hot path; see graph_runtime_relations.py for
        the (pure, testable) entity-resolution and planning logic.

        Single-valued relations expire the edge they replace (#656) rather than
        adding a rival, so retrieval stops having to choose between "works at
        Harbor Dynamics" and "works at Redwing Marine" by embedding score.
        """
        ego_id = self.retrieval.ego_node_id()
        if not ego_id:
            return
        planned = plan_runtime_relations(content, self._graph.nodes(data=True), ego_id)
        if not planned:
            return

        now = datetime.now(timezone.utc).isoformat()
        for stale in plan_supersessions(
            planned, self._graph.edges(keys=True, data=True), ego_id
        ):
            self._graph.edges[stale.source_id, stale.target_id, stale.relation]["valid_to"] = now
            logger.info(
                f"[ GRAPH ] Superseded {stale.source_id} -{stale.relation}-> "
                f"{stale.target_id} (valid_to={now})."
            )

        for edge in planned:
            self._ensure_entity(edge.entity_id, entity_type=edge.entity_type, name=edge.name)
            await self.upsert_inferred_edge(edge.source_id, edge.target_id, edge.relation)

    async def describe_supersession(self, content: str) -> Optional[str]:
        """What live typed relation would saving `content` replace? (read-only)

        Lets MemoryWriteGate flag a supersession structurally, which is the whole
        point of #656: "Alex now works at Redwing Marine" scores only 0.43
        against the employer fact it replaces -- inside the genuinely-new band,
        with the wrong nearest neighbour -- so no embedding threshold can see it.
        A typed single-valued edge can.
        """
        if not self._is_initialized:
            await self.initialize()
        ego_id = self.retrieval.ego_node_id()
        if not ego_id:
            return None
        planned = plan_runtime_relations(content, self._graph.nodes(data=True), ego_id)
        stale = plan_supersessions(planned, self._graph.edges(keys=True, data=True), ego_id)
        if not stale:
            return None
        return self.retrieval.node_label(stale[0].old_value_id)

    # Always-on singleton nodes: injected verbatim into every user-facing prompt,
    # excluded from retrieval. Reading them is graph_always_on.py's job (sync,
    # mtime-cached, off the wake path); this module still owns writing them.
    _CORE_PROFILE_NODE_ID = CORE_PROFILE_NODE_ID
    _BEHAVIOR_NODE_ID = BEHAVIOR_NODE_ID

    async def get_core_profile(self) -> Optional[str]:
        """Cheap always-on read — no LLM, no walk. Returns None if never written."""
        if not self._is_initialized:
            await self.initialize()
        data = self._graph.nodes.get(self._CORE_PROFILE_NODE_ID)
        if not data or not data.get("valid", True):
            return None
        return data.get("content") or None

    @classmethod
    def read_core_profile_sync(cls) -> Optional[str]:
        """The always-on identity block. See graph_always_on.read_always_on."""
        return read_always_on(cls._CORE_PROFILE_NODE_ID)

    @classmethod
    def read_behavior_sync(cls) -> Optional[str]:
        """The always-on user behavior directives. See graph_always_on.read_always_on."""
        return read_always_on(cls._BEHAVIOR_NODE_ID)

    def _sync_seed_facts_from_markdown(self) -> None:
        """Reconcile `source: seed` fact nodes with personal_knowledge.md.

        Two things this fixes. Seeding only ever ran on an EMPTY graph, so edits to
        personal_knowledge.md needed a manual delete of knowledge_graph.json to take
        effect — including removals. And pre-atomization graphs stored a whole
        `## section` as one node, so retrieval returned the section as a unit and the
        120-char compaction picked which sentence survived ("what still needs booking"
        got the wedding DATE; the dog's name was truncated off the family blob).

        Only `source: seed` nodes are touched — runtime saves (manual / learn
        directive / reflection) are never reconciled away. A fact the user has
        `forget`-ed stays invalid: the flag is carried over by content match, so
        re-seeding cannot resurrect it.
        """
        desired = {f["id"]: f for f in SEED_FACTS if (f.get("content") or "").strip()}
        if not desired:
            return  # no markdown (or all placeholders) — never wipe a live graph

        existing = [
            (nid, data)
            for nid, data in self._graph.nodes(data=True)
            if data.get("kind", "fact") == "fact" and data.get("source") == "seed"
        ]
        valid_by_content = {
            (data.get("content") or "").strip(): data.get("valid", True)
            for _, data in existing
        }
        existing_ids = {nid for nid, _ in existing}

        now = datetime.now(timezone.utc).isoformat()
        removed = added = 0

        for nid in existing_ids - set(desired):
            self._graph.remove_node(nid)
            removed += 1

        for fid, fact in desired.items():
            content = fact["content"].strip()
            node = self._graph.nodes.get(fid)
            if node and (node.get("content") or "").strip() == content:
                continue
            self._graph.add_node(
                fid,
                content=content,
                topic=fact["topic"],
                source="seed",
                kind="fact",
                # Re-adding a fact the user forgot must not bring it back.
                valid=valid_by_content.get(content, True),
                created_at=(node or {}).get("created_at") or now,
            )
            added += 1

        if removed or added:
            self._save_graph()
            self._query_cache.clear()
            logger.info(
                f"[ GRAPH ] Seed facts synced from personal_knowledge.md "
                f"(+{added} / -{removed}, {len(desired)} total)."
            )

    def _sync_core_profile_from_markdown(self) -> None:
        """Make the reserved markdown sections the source of truth for always-on nodes.

        Runs on every initialize() so editing personal_knowledge.md takes effect on
        restart without deleting the graph. A profile written by sleep-cognify (which
        is human-reviewed before it lands) is left alone — the markdown only owns a
        node while that node is markdown-sourced or absent.
        """
        for node_id, kind, text in (
            (self._CORE_PROFILE_NODE_ID, "core_profile", SEED_CORE_PROFILE),
            (self._BEHAVIOR_NODE_ID, "behavior", SEED_BEHAVIOR),
        ):
            text = (text or "").strip()
            existing = self._graph.nodes.get(node_id)
            if existing and existing.get("source") not in (None, "personal_knowledge"):
                continue
            if not text:
                continue
            if existing and (existing.get("content") or "").strip() == text[:always_on_max_chars()]:
                continue
            self._write_always_on(node_id, kind, text, source="personal_knowledge")
            logger.info(f"[ GRAPH ] {kind} synced from personal_knowledge.md.")

    async def set_core_profile(self, text: str, *, max_chars: int = 800) -> str:
        """Sleep-cognify-gated rewrite of the always-on core profile block.

        Hard-capped to `max_chars` (never grows unbounded across nightly cycles) and
        stored as a singleton node so the wake-path read stays a single dict lookup.
        Callers (sleep_cognify.py) only reach this after human review/approval — the
        core profile is always-on context, so its rewrite is never auto-applied.
        """
        return self._write_always_on(
            self._CORE_PROFILE_NODE_ID, "core_profile", text,
            source="sleep_cognify", max_chars=max_chars,
        )

    def _write_always_on(
        self, node_id: str, kind: str, text: str, *,
        source: str, max_chars: Optional[int] = None,
    ) -> str:
        capped = (text or "").strip()[: max_chars or always_on_max_chars()]
        now = datetime.now(timezone.utc).isoformat()
        self._graph.add_node(
            node_id,
            kind=kind,
            content=capped,
            topic=kind,
            source=source,
            created_at=now,
            updated_at=now,
            valid=True,
        )
        self._save_graph()
        self._query_cache.clear()
        logger.info(f"[ GRAPH ] {kind} rewritten ({len(capped)} chars).")
        return capped

    # ------------------------------------------------------------------
    # Graph building helpers
    # ------------------------------------------------------------------

    def _seed_graph(self):
        now = datetime.now(timezone.utc).isoformat()
        for fact in SEED_FACTS:
            self._graph.add_node(
                fact["id"],
                kind="fact",
                content=fact["content"],
                topic=fact["topic"],
                source="seed",
                created_at=now,
                valid=True,
            )

        for entity in SEED_ENTITIES:
            self._upsert_entity_node(entity, created_at=now, source="seed")

        for edge in SEED_EDGES:
            self._upsert_curated_edge(edge)

        # Soft related edges between fact nodes are still created at runtime by
        # _link_related_nodes. Curated ## _graph edges are the typed layer (#608).

        self._save_graph()
        logger.info(
            f"[ GRAPH ] Seeded {len(SEED_FACTS)} facts, "
            f"{len(SEED_ENTITIES)} entities, {len(SEED_EDGES)} curated edges."
        )

    def _upsert_entity_node(
        self,
        entity: GraphEntity,
        *,
        created_at: str,
        source: str = "seed",
    ) -> None:
        """Add or refresh an entity node without clobbering invalidation."""
        if entity.id in self._graph and not self._graph.nodes[entity.id].get("valid", True):
            return
        existing = self._graph.nodes.get(entity.id, {})
        self._graph.add_node(
            entity.id,
            kind="entity",
            entity_type=entity.entity_type,
            name=entity.name,
            content=entity.name,
            topic=entity.entity_type.lower(),
            source=existing.get("source", source),
            created_at=existing.get("created_at", created_at),
            # Markdown owns this: dropping an attribute clause from _graph should
            # remove it, so an absent value is not backfilled from `existing`.
            attributes=entity.attributes,
            valid=True,
        )

    def _upsert_curated_edge(self, edge: GraphEdge) -> None:
        """Add a curated typed edge keyed by relation (MultiDiGraph)."""
        if edge.source_id not in self._graph or edge.target_id not in self._graph:
            logger.warning(
                f"[ GRAPH ] Skipping edge {edge.source_id} -{edge.relation}-> "
                f"{edge.target_id}: missing endpoint node."
            )
            return
        attrs = {
            "relation": edge.relation,
            "confidence": "curated",
            "valid_from": edge.valid_from,
            "valid_to": edge.valid_to,
        }
        key = edge.relation
        if self._graph.has_edge(edge.source_id, edge.target_id, key=key):
            prev = self._graph.edges[edge.source_id, edge.target_id, key]
            if prev.get("confidence") == "curated" and prev.get("relation") == edge.relation:
                return
            self._graph.edges[edge.source_id, edge.target_id, key].update(attrs)
        else:
            self._graph.add_edge(edge.source_id, edge.target_id, key=key, **attrs)

    def _link_related_nodes(self, new_node_id: str, topic: str, content: str):
        """Connect a new fact node to existing fact nodes that share topic/keywords."""
        content_words = set(content.lower().split())
        for nid, data in self._graph.nodes(data=True):
            if nid == new_node_id or not data.get("valid", True):
                continue
            if data.get("kind") == "entity":
                continue
            same_topic = data.get("topic") == topic and topic
            existing_words = set(data.get("content", "").lower().split())
            overlap = len(content_words & existing_words) / max(len(content_words), 1)
            if same_topic or overlap >= 0.25:
                if self._graph.has_edge(new_node_id, nid, key="related"):
                    continue
                self._graph.add_edge(
                    new_node_id,
                    nid,
                    key="related",
                    relation="related",
                    confidence="inferred",
                    valid_from=None,
                    valid_to=None,
                )

    def _detect_topic(self, content: str) -> str:
        """Simple keyword-based topic detection — no LLM call needed."""
        content_lower = content.lower()
        topic_keywords = {
            "relationship": ["wedding", "partner", "spouse", "fiancée", "fiancee", "ceremony", "venue", "bride"],
            "health": ["health", "medical", "doctor", "symptoms", "diagnosis", "medication", "flare", "fatigue"],
            "budget": ["budget", "money", "savings", "cost", "expense", "financial", "car"],
            "gaming": ["gaming", "minecraft", "speedrun", "elo", "game", "pb", "seed"],
            "work": ["code", "project", "github", "deploy", "bug", "feature", "work"],
            "identity": ["user", "chad", "assistant", "windows", "lm studio"],
        }
        for topic, keywords in topic_keywords.items():
            if any(kw in content_lower for kw in keywords):
                return topic
        return "general"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Returns a summary dict — ask Chad 'what does your knowledge graph look like?' to see this."""
        if not self._is_initialized:
            return {"status": "not initialized"}
        valid_facts = [
            (nid, d)
            for nid, d in self._graph.nodes(data=True)
            if d.get("valid", True) and d.get("kind", "fact") == "fact"
        ]
        valid_entities = sum(
            1
            for _, d in self._graph.nodes(data=True)
            if d.get("valid", True) and d.get("kind") == "entity"
        )
        topics = {}
        for _, d in valid_facts:
            t = d.get("topic", "general")
            topics[t] = topics.get(t, 0) + 1
        return {
            "status": "active",
            "total_facts": len(valid_facts),
            "total_entities": valid_entities,
            "relationships": self._graph.number_of_edges(),
            "topics": topics,
            "graph_file": self._graph_path,
        }

    def list_active_facts(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List valid fact + observation nodes for the Memory UI (entities excluded)."""
        nodes = [
            (nid, data)
            for nid, data in self._graph.nodes(data=True)
            if data.get("valid", True)
            and data.get("kind", "fact") in RETRIEVABLE_KINDS
            and data.get("content")
        ]
        nodes.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
        page = nodes[offset : offset + limit]
        return [
            {
                "id": str(nid),
                "content": data.get("content", ""),
                "type": data.get("topic", "general"),
                "timestamp": data.get("created_at"),
                "source": "graph" if data.get("kind", "fact") == "fact" else "graph:observation",
            }
            for nid, data in page
        ]

    def count_active_facts(self) -> int:
        return sum(
            1
            for _, data in self._graph.nodes(data=True)
            if data.get("valid", True)
            and data.get("kind", "fact") in RETRIEVABLE_KINDS
            and data.get("content")
        )

    def list_relationships(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List knowledge-graph edges for the Memory UI."""
        edges: list[dict] = []
        for source_id, target_id, _key, edge_data in self._graph.edges(keys=True, data=True):
            source = self._graph.nodes.get(source_id, {})
            target = self._graph.nodes.get(target_id, {})
            if not source.get("valid", True) or not target.get("valid", True):
                continue
            src_text = (
                source.get("name")
                or source.get("content")
                or source_id
            )
            tgt_text = (
                target.get("name")
                or target.get("content")
                or target_id
            )
            src_text = str(src_text)[:120]
            tgt_text = str(tgt_text)[:120]
            relation = edge_data.get("relation", "related")
            confidence = edge_data.get("confidence", "inferred")
            edges.append({
                "id": f"{source_id}::{relation}::{target_id}",
                "content": f"{src_text} -{relation}-> {tgt_text}",
                "type": relation,
                "timestamp": source.get("created_at") or target.get("created_at"),
                "source": "graph",
                "confidence": confidence,
            })
        edges.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return edges[offset : offset + limit]

    def count_relationships(self) -> int:
        count = 0
        for source_id, target_id, _key, _data in self._graph.edges(keys=True, data=True):
            source = self._graph.nodes.get(source_id, {})
            target = self._graph.nodes.get(target_id, {})
            if source.get("valid", True) and target.get("valid", True):
                count += 1
        return count

    def _save_graph(self):
        try:
            data = nx.node_link_data(self._graph)
            # Strip cached embeddings from the saved file — they're large and recomputable
            for node in data.get("nodes", []):
                node.pop("_embedding", None)
            with open(self._graph_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            mirror_facts_to_supabase(self._graph)
        except Exception as e:
            logger.error(f"[ GRAPH ] Save failed: {e}")

    def _load_graph(self):
        self.retrieval.invalidate_caches()  # node set is about to change
        if not os.path.exists(self._graph_path):
            return
        try:
            with open(self._graph_path, encoding="utf-8") as f:
                data = json.load(f)
            self._graph = nx.node_link_graph(data, directed=True, multigraph=True)
            if not isinstance(self._graph, nx.MultiDiGraph):
                self._graph = nx.MultiDiGraph(self._graph)
            logger.info(f"[ GRAPH ] Loaded graph from {self._graph_path}.")
        except Exception as e:
            logger.warning(f"[ GRAPH ] Could not load graph ({e}). Starting fresh.")
            self._graph = nx.MultiDiGraph()

    def _migrate_legacy_schema(self) -> None:
        """Backfill kind/confidence on pre-#608 graphs without dropping data."""
        if self._graph.number_of_nodes() == 0 and self._graph.number_of_edges() == 0:
            return

        changed = False
        for _nid, data in self._graph.nodes(data=True):
            if "kind" not in data:
                data["kind"] = "fact"
                changed = True

        # Rebuild edges with relation as MultiDiGraph key when needed.
        edge_snapshot = list(self._graph.edges(keys=True, data=True))
        for u, v, key, data in edge_snapshot:
            rel = data.get("relation", "related")
            if "relation" not in data:
                data["relation"] = "related"
                rel = "related"
                changed = True
            if "confidence" not in data:
                data["confidence"] = (
                    "inferred" if data.get("relation", "related") == "related" else "curated"
                )
                changed = True
            if "valid_from" not in data:
                data["valid_from"] = None
                changed = True
            if "valid_to" not in data:
                data["valid_to"] = None
                changed = True
            if key != rel:
                attrs = dict(data)
                self._graph.remove_edge(u, v, key=key)
                self._graph.add_edge(u, v, key=rel, **attrs)
                changed = True

        if changed:
            self._save_graph()
            logger.info("[ GRAPH ] Migrated legacy knowledge_graph.json schema (kind/confidence).")

    # ------------------------------------------------------------------
    # Embeddings (reuses existing local embedding stack)
    # ------------------------------------------------------------------

    def _get_embedding(self, text: str) -> Optional[list]:
        try:
            if self._embedding_model is None:
                from src.core.intelligence.local_embeddings import get_embedding_model
                self._embedding_model = get_embedding_model()
            result = self._embedding_model.encode(text)
            return result if isinstance(result, list) else list(result)
        except Exception:
            return None
