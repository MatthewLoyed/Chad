"""Query-time retrieval and ranking over the personal knowledge graph.

Split out of `graph_rag_client.py`, which had grown past this repo's module
ceiling by carrying two unrelated jobs: owning the graph (load, seed, write,
persist, migrate) and *querying* it. This module is the second job only.

Given a query and a graph, produce the budgeted bullet block that gets injected
into the prompt:

    seed entities from the query
      -> walk typed edges outward, ranked
      -> fill remaining budget with fact similarity
      -> compact to the item/char budget

No LLM call on this path, and no writes: the retriever never mutates the graph
except to memoize node embeddings.

The graph is passed as a *getter*, not a reference — `GraphRAGClient` replaces
`self._graph` wholesale on reload, and probes swap it to point at another
checkout's file. Embedding is likewise a callable so monkeypatching
`GraphRAGClient._get_embedding` in tests still reaches this code.

Ranking history worth not re-learning: unboosted walk edges used to fall through
to an ALPHABETICAL tiebreak on relation name, which sorted `partner_of` last;
and topic hints used to FLOOR a fact's score to a flat 0.70, putting a whole
topic above any threshold with its internal ranking destroyed. See
docs/architecture/MEMORY_AUDIT_2026-07.md.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Callable, Optional

import networkx as nx

from src.core.intelligence.personal_knowledge_parse import is_placeholder_text, name_tokens
from src.shared.domain_scope import record_matches_domain

logger = logging.getLogger("GraphRetrieval")

# Node kinds that participate in fact-style retrieval. Observations (#611,
# sleep-cognify-consolidated beliefs) retrieve exactly like raw facts. Entity,
# core_profile and behavior nodes are excluded — they surface via the typed-edge
# walk / the always-on prompt blocks instead.
RETRIEVABLE_KINDS = ("fact", "observation")

# Intrinsic importance of a relation, independent of what the query happened to
# say. Lower sorts first.
RELATION_PRIORITY = {
    "partner_of": 0,
    "parent_of": 1,
    "child_of": 1,
    "sibling_of": 1,
    "lives_in": 2,
    "owns": 2,
    "employed_by": 2,
    "member_of": 3,
    "works_with": 3,
    "related_to_project": 3,
    "best_man_of": 4,
    "groomsman_of": 4,
    "friend_of": 5,
    "attended": 6,
}
DEFAULT_RELATION_PRIORITY = 5

# Generic category words -> the entity TYPE they refer to (#661). Lets a query
# that names no entity ("what documents relate to the wedding") still reach the
# right nodes on ANY graph, replacing hardcoded per-user node ids.
#
# Every term is deliberately a common noun. Terms that are also everyday coding
# vocabulary are deliberately EXCLUDED — "file"/"files"/"doc"/"docs" would make
# "what files changed" seed the user's Drive catalog into a code review.
_CATEGORY_ENTITY_TYPES: dict[str, frozenset] = {
    "Document": frozenset({
        "document", "documents", "folder", "folders", "spreadsheet", "roadmap", "drive",
    }),
    "Project": frozenset({"project", "projects"}),
    "Event": frozenset({"event", "events"}),
}
# Per type, per query. Bounds the blast radius when a user has a large catalog:
# without it, "my documents" would seed every Document node in the graph.
_CATEGORY_SEED_CAP = 3

# Multiplier applied to a fact's similarity when the query mentions its topic.
# Deliberately a boost and not a floor — see `score_nodes`.
TOPIC_HINT_BOOST = 1.35


class GraphRetriever:
    """Ranks and formats knowledge-graph content for one query."""

    def __init__(
        self,
        graph_getter: Callable[[], nx.MultiDiGraph],
        embed: Callable[[str], Optional[list]],
    ) -> None:
        self._graph_getter = graph_getter
        self._embed = embed
        # "" = computed, no Person entity found. None = not computed yet.
        self._ego_cache: Optional[str] = None

    @property
    def _graph(self) -> nx.MultiDiGraph:
        return self._graph_getter()

    def invalidate_caches(self) -> None:
        """Call when the underlying node set changes (reload, re-seed)."""
        self._ego_cache = None

    def query(self, query: str, domain: Optional[str] = None) -> Optional[str]:
        """
        Hybrid retrieve:
        1. Seed entity nodes from the query, walk typed edges (1–2 hops).
        2. Fall back / fill with fact embedding (or keyword) similarity.
        Returns a budgeted bullet list — no LLM on this path.

        `domain` scopes the result to a domain agent: tagged-for-this-domain plus untagged
        global facts, never another domain's. Both retrieval halves must honour it — the
        typed-edge walk can otherwise reach a scoped fact through an untagged entity.
        """
        from src.shared.config_manager import ConfigManager

        max_results = ConfigManager.get("knowledge_graph.max_results", 6)
        threshold = ConfigManager.get("knowledge_graph.relevance_threshold", 0.25)
        max_item_chars = ConfigManager.get("knowledge_graph.max_item_chars", 120)
        max_boost_chars = ConfigManager.get("knowledge_graph.max_boost_chars", 800)
        walk_hops = int(ConfigManager.get("knowledge_graph.walk_hops", 2) or 2)

        walk_lines = self.collect_walk_lines(query, max_hops=walk_hops, domain=domain)
        fact_hits = self.collect_fact_hits(query, threshold=threshold, domain=domain)

        return self.format_boost(
            walk_lines,
            fact_hits,
            query=query,
            max_items=max_results,
            max_item_chars=max_item_chars,
            max_boost_chars=max_boost_chars,
            min_similarity=threshold,
        )

    def collect_fact_hits(self, query: str, *, threshold: float,
                          domain: Optional[str] = None) -> list[dict]:
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
            return []
        scored = self.score_nodes(query, valid_nodes)
        top = sorted(scored, key=lambda x: x[2], reverse=True)
        return [
            {"content": data["content"], "similarity": score, "metadata": data.get("metadata")}
            for _, data, score in top
            if score >= threshold
        ]

    def seed_entity_ids(self, query: str, domain: Optional[str] = None) -> list[str]:
        """Match query tokens against entity names / ids, honouring the active domain scope."""
        q = query.lower()
        q_words = set(re.findall(r"[a-z0-9]+", q))
        # Kept before stop-word removal: the first-person check below needs
        # exactly the pronouns that `stop` is about to discard.
        raw_words = set(q_words)
        # Drop ultra-common query filler so "who is <name>" still seeds that name.
        stop = {
            "who", "what", "where", "when", "why", "how", "is", "are", "was", "were",
            "the", "a", "an", "my", "me", "i", "to", "of", "for", "and", "or", "in",
            "on", "at", "do", "does", "did", "about", "tell", "with", "from",
            "next", "step", "steps",
        }
        q_words -= stop

        scored: list[tuple[str, float]] = []
        for nid, data in self._graph.nodes(data=True):
            if not data.get("valid", True) or data.get("kind") != "entity":
                continue
            if not record_matches_domain(data, domain):
                continue
            name = (data.get("name") or "").strip()
            name_l = name.lower()
            # CamelCase ids (WeddingRoadmap) must tokenize so "roadmap" matches.
            name_words = self.entity_tokens(f"{name} {nid}")
            if name_l and name_l in q:
                scored.append((str(nid), 1.0))
                continue
            if not q_words:
                continue
            overlap = len(q_words & name_words)
            if overlap:
                scored.append((str(nid), overlap / max(len(q_words), 1)))

        # Ego / topic shortcuts when the query never names an entity. Every term
        # here must be generic English: a proper noun would only ever fire on one
        # person's graph, and belongs in that person's (gitignored)
        # personal_knowledge.md as an entity name, which the loop above already
        # matches for free.
        ego_id = self.ego_node_id()
        if ego_id in self._graph and self._graph.nodes[ego_id].get("valid", True):
            ego_triggers = (
                "groomsmen", "groomsman", "best man", "bestman", "wedding party",
                "my family", "my friends", "my fiancée", "my fiancee", "my fiancé",
                "my fiance", "youtube", "my channel", "my channels",
                "my wedding", "our wedding", "where do i work",
                "my job", "my internship",
            )
            if any(t in q for t in ego_triggers):
                scored.append((ego_id, 0.95))

        # Life-area shortcuts. These seed the EGO node and let the typed walk find
        # the user's own school/work entities — the previous version listed specific
        # node ids (one user's university, one user's employer), so it did nothing on
        # anyone else's graph and put real names in tracked source.
        school_terms = {"graduate", "graduation", "bachelor", "master", "masters",
                        "school", "degree", "college", "university", "alma"}
        # "work" alone is too broad (matches "workout" / "how X works"); require a
        # workish phrase or term. Phrases cover "who do I work with", which names no
        # entity and so previously produced no seed and no works_with edge at all.
        work_terms = {"intern", "internship", "job", "employer", "coworker", "coworkers",
                      "colleague", "colleagues", "teammate", "teammates"}
        work_phrases = ("where do i work", "my job", "at work", "i work with",
                        "work with", "my team", "my office", "my boss")

        # "Where did I USED TO work" names no entity and matches no work phrase,
        # so without this it produced no seed and the expired edge was
        # unreachable — history that exists but can never be asked for (#656).
        # Deliberately requires a first-person word as well as the past-tense
        # marker: "the PREVIOUS function returns None" is a coding question, and
        # seeding ego there is the context-pollution bug, not a recall fix.
        first_person = {"i", "me", "my", "mine", "we", "us", "our", "ours"}
        personal_history = self.wants_history(q) and bool(first_person & raw_words)

        if (
            school_terms & q_words
            or any(t in q for t in ("bachelor's", "master's"))
            or work_terms & q_words
            or any(t in q for t in work_phrases)
            or personal_history
        ) and ego_id in self._graph and self._graph.nodes[ego_id].get("valid", True):
            scored.append((ego_id, 0.92))

        # Category queries ("what documents...", "my projects") name no entity, so
        # seed by entity TYPE and let the walk do the rest. This replaces three
        # branches that listed one specific user's node ids and channel names —
        # dead code on anyone else's graph, and real names in tracked source.
        #
        # A branch keyed on a project's actual name was deleted outright rather
        # than generalized: naming an entity already scores 1.0 in the loop above,
        # so it never did anything the general path doesn't.
        for entity_type, terms in _CATEGORY_ENTITY_TYPES.items():
            if not (terms & q_words):
                continue
            typed: list[tuple[int, str]] = []
            for nid, data in self._graph.nodes(data=True):
                if data.get("kind") != "entity" or not data.get("valid", True):
                    continue
                if data.get("entity_type") != entity_type:
                    continue
                if not record_matches_domain(data, domain):
                    continue
                overlap = len(q_words & self.entity_tokens(f"{data.get('name', '')} {nid}"))
                typed.append((overlap, str(nid)))
            # Named ones first, then a bounded sample so a big Drive catalog can't
            # flood the seed list. Sorted by id for determinism.
            typed.sort(key=lambda item: (-item[0], item[1]))
            for overlap, nid in typed[:_CATEGORY_SEED_CAP]:
                scored.append((nid, 0.93 if overlap else 0.60))

        # Seed the ego node alongside any other match, at a score low enough that it
        # never displaces one. Naming another person ("plan a date with <partner>")
        # used to seed only that person, so the edge JOINING them to the user — the
        # whole reason the query mentions them — was never preferred.
        #
        # Gated on there being another seed: seeding ego unconditionally walks the
        # user's family tree into EVERY turn, including "fix this traceback".
        if scored and ego_id in self._graph and self._graph.nodes[ego_id].get("valid", True):
            scored.append((ego_id, 0.25))

        scored.sort(key=lambda x: x[1], reverse=True)
        # Dedupe preserving score order
        seen: set[str] = set()
        out: list[str] = []
        for nid, score in scored:
            if score < 0.2 or nid in seen:
                continue
            seen.add(nid)
            out.append(nid)
            if len(out) >= 8:
                break
        return out

    def ego_node_id(self) -> Optional[str]:
        """Graph node representing the user themself.

        Auto-detected as the most-connected Person entity — in your own knowledge
        graph, that is you — so no real name has to be hardcoded in tracked source.
        Set `knowledge_graph.ego_node_id` to pin it explicitly (households where
        another person is better connected, or a graph with no Person entities).
        """
        try:
            from src.shared.config_manager import ConfigManager

            configured = ConfigManager.get("knowledge_graph.ego_node_id", None)
            if configured and str(configured).strip():
                return str(configured).strip()
        except Exception:
            pass

        if self._ego_cache is not None:
            return self._ego_cache or None

        best: Optional[str] = None
        best_degree = -1
        for nid, data in self._graph.nodes(data=True):
            if data.get("kind") != "entity" or not data.get("valid", True):
                continue
            if not str(nid).startswith("Person:"):
                continue
            degree = self._graph.degree(nid)
            if degree > best_degree:
                best, best_degree = str(nid), degree
        self._ego_cache = best or ""
        return best

    @staticmethod
    def entity_tokens(blob: str) -> set[str]:
        """Tokenize entity ids/names including CamelCase (WeddingRoadmap → wedding, roadmap).

        Canonical implementation lives in personal_knowledge_parse alongside
        `entity_node_id`, since entity resolution on the WRITE path (#657/#656)
        needs exactly the same notion of "same name".
        """
        return name_tokens(blob)

    def node_label(self, node_id: str) -> str:
        data = self._graph.nodes.get(node_id, {})
        name = (data.get("name") or "").strip()
        if name:
            return name
        content = (data.get("content") or "").strip()
        if content and data.get("kind") != "fact":
            return content.split("\n", 1)[0][:80]
        sid = str(node_id)
        if ":" in sid:
            return sid.split(":", 1)[1]
        return sid

    def annotated_label(
        self, node_id: str, *, extra: tuple[str, ...] = (), ego_id: Optional[str] = None
    ) -> str:
        """Node label plus any inline detail, e.g. `Northgate (Corvallis, Oregon)`.

        Without this a walk line is just two bare names and a relation — true, but
        not actionable, while still costing a slot in the identity budget (#659).

        The ego node is deliberately never annotated: it is the user, whose details
        are already in the always-on core profile, so repeating them on every line
        of every walk would spend budget to say what the model has been told twice
        already. Callers in a loop should pass `ego_id` — `ego_node_id()` re-reads
        config on every call, and this runs twice per edge on the turn path.
        """
        label = self.node_label(node_id)
        notes: list[str] = []
        if node_id != (ego_id if ego_id is not None else self.ego_node_id()):
            attributes = (self._graph.nodes.get(node_id, {}).get("attributes") or "").strip()
            if attributes:
                notes.append(attributes)
        notes.extend(item for item in extra if item)
        return f"{label} ({', '.join(notes)})" if notes else label

    def edge_is_walkable(self, edge_data: dict, *, include_expired: bool = False) -> bool:
        relation = edge_data.get("relation", "related")
        if relation == "related":
            return False
        if edge_data.get("valid_to") and not include_expired:
            return False
        return True

    # A past-tense cue alone is NOT enough to mean "ask about the user's past":
    # "my PREVIOUS implementation", "my FORMER approach", "my regex-based parser"
    # are all coding questions, and treating them as historical seeds the ego node
    # into the turn — the context-pollution bug, reintroduced. So a cue only counts
    # when it co-occurs with a personal-life term. (Measured: all four of those
    # examples matched a bare-cue check.)
    _HISTORY_CUES = (
        "used to", "use to", "previously", "previous", "former", "formerly",
        "no longer", "back then", "in the past",
    )
    _HISTORY_LIFE_TERMS = (
        "work", "worked", "job", "career", "employer", "employed", "company",
        "live", "lived", "living", "partner", "married", "school", "studied",
    )

    @classmethod
    def wants_history(cls, query: str) -> bool:
        """Is the user explicitly asking about their own past? (#656)

        Expired edges are excluded from the walk by default -- "where do I work"
        must not get the old employer back. But "where did I USED TO work" is
        unanswerable without them, and answering it is the only reason history is
        kept, so it takes both an explicit past-tense cue and a personal-life
        subject.
        """
        q = (query or "").lower()
        return any(cue in q for cue in cls._HISTORY_CUES) and any(
            term in q for term in cls._HISTORY_LIFE_TERMS
        )

    def collect_walk_lines(self, query: str, *, max_hops: int,
                           domain: Optional[str] = None) -> list[str]:
        seeds = self.seed_entity_ids(query, domain=domain)
        if not seeds:
            return []

        include_expired = self.wants_history(query)
        walk_ego = self.ego_node_id()  # hoisted: re-reads config on every call
        q = query.lower()
        q_words = set(re.findall(r"[a-z0-9]+", q))
        # Boost edges whose relation matches what the user is asking about.
        relation_boosts = (
            (("fiancé", "fiance", "fiancée", "fiancee", "partner", "girlfriend",
              "boyfriend", "wife", "husband", "significant other"),
             ("partner_of",)),
            (("family", "father", "mother", "dad", "mom", "brother", "sister",
              "parent", "sibling", "son", "daughter"),
             ("parent_of", "child_of", "sibling_of", "partner_of")),
            (("groomsmen", "groomsman", "best man", "bestman", "wedding party"),
             ("best_man_of", "groomsman_of", "friend_of")),
            (("wedding", "venue", "married", "ceremony"),
             ("happened_at", "attended", "located_at", "in_service_of", "occurs_on", "about")),
            (("where", "live", "lives", "location", "from"),
             ("lives_in", "located_at")),
            (("project", "job", "owns", "cto", "business", "intern"),
             ("owns", "member_of", "employed_by", "related_to_project", "works_with")),
            (("colleague", "coworker", "teammate", "team", "work with", "my boss"),
             ("works_with", "member_of", "employed_by")),
            # Employer-specific phrases only. Deliberately NOT a bare "work":
            # that matches "how tcp handshakes work" and drags the whole career
            # subgraph into unrelated turns (the `neg-tcp` regression).
            (("where do i work", "my job", "employer", "employed", "my company"),
             ("employed_by", "member_of")),
            # "university"/"college"/"campus" seed the walk via school_terms in
            # seed_entity_ids but were missing here, so the `attended` edge they
            # were meant to surface ranked below every family edge on its
            # intrinsic priority. The two vocabularies have to agree.
            (("graduate", "graduation", "school", "degree", "bachelor", "master",
              "university", "college", "campus", "studied", "alma"),
             ("attended", "located_at", "member_of")),
            (("roadmap", "document", "documents", "folder", "drive", "doc"),
             ("about", "in_service_of", "sourced_from", "mentions")),
            # "Help me do something for/with the person I love" — the single most
            # common personalization ask, and previously the one with no vocabulary
            # at all, so naming the partner instead of their role lost the edge.
            (("date", "date night", "anniversary", "romantic", "valentine", "propose"),
             ("partner_of",)),
            (("gift", "gifts", "birthday", "surprise", "celebrate"),
             ("partner_of", "parent_of", "sibling_of", "child_of")),
            (("youtube", "channel", "channels"),
             ("owns",)),
            (("friend", "friends"),
             ("friend_of",)),
        )

        def edge_rank(relation: str, src: str, tgt: str) -> tuple[int, int]:
            rel = relation.lower()
            endpoints = f"{self.node_label(src)} {self.node_label(tgt)} {src} {tgt}".lower()
            for keywords, rels in relation_boosts:
                if rel not in rels:
                    continue
                if not any(k in q for k in keywords):
                    continue
                # Prefer edges whose endpoints also mention a query keyword, so
                # naming one project doesn't boost an unrelated membership edge.
                topic_hit = any(
                    k in endpoints
                    for k in keywords
                    if len(k) >= 4 and k not in {
                        "work", "project", "job", "owns", "where", "live", "lives",
                        "from", "friend", "friends", "family", "parent", "channel",
                        "channels", "wedding", "venue", "married", "ceremony",
                        "business",
                    }
                )
                # Always treat explicit entity/project names in the query as topic.
                name_hit = any(w in endpoints for w in q_words if len(w) >= 4)
                if topic_hit or name_hit or rel in {
                    "partner_of", "best_man_of", "groomsman_of", "parent_of",
                    "sibling_of", "child_of",
                }:
                    return (0, 0 if (topic_hit or name_hit) else 1)
                return (1, 1)
            return (2, 0)

        # Sort key: query-intent match first, then how close the edge is to a seed
        # entity, then the relation's intrinsic importance. The relation NAME is only
        # a final determinism tiebreak — it used to be the *effective* tiebreak for
        # every unboosted edge, which sorted `partner_of` to the bottom alphabetically.
        scored_lines: list[tuple[tuple[int, int, int, int, str], str]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        queue: deque[tuple[str, int]] = deque((sid, 0) for sid in seeds)
        visited: set[str] = set(seeds)

        while queue:
            nid, depth = queue.popleft()
            if depth >= max_hops:
                continue

            neighbors: list[tuple[str, str, dict]] = []
            for _, tgt, _key, edata in self._graph.out_edges(nid, keys=True, data=True):
                neighbors.append((nid, tgt, edata))
            for src, _, _key, edata in self._graph.in_edges(nid, keys=True, data=True):
                neighbors.append((src, nid, edata))

            for src, tgt, edata in neighbors:
                if not self.edge_is_walkable(edata, include_expired=include_expired):
                    continue
                src_data = self._graph.nodes.get(src, {})
                tgt_data = self._graph.nodes.get(tgt, {})
                if not src_data.get("valid", True) or not tgt_data.get("valid", True):
                    continue
                # Scope both endpoints, not just the one we walked from: an untagged entity
                # is reachable from a general chat, and following its edge to a domain-tagged
                # node would surface that node's label — a leak through the back door.
                if not (record_matches_domain(src_data, domain)
                        and record_matches_domain(tgt_data, domain)):
                    continue
                relation = edata.get("relation", "related")
                key = (src, relation, tgt)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                # An expired edge only ever appears on an explicitly historical
                # query, and MUST say so — an unmarked line reads as current and
                # would be worse than omitting it. Shares the target's
                # parenthetical so an attributed, expired edge reads as
                # `Tidewater (marine engineering, until 2026-02)` rather than
                # trailing two separate bracketed groups.
                expired_at = edata.get("valid_to")
                until = (f"until {str(expired_at)[:10]}",) if expired_at else ()
                line = (
                    f"{self.annotated_label(src, ego_id=walk_ego)} -{relation}-> "
                    f"{self.annotated_label(tgt, extra=until, ego_id=walk_ego)}"
                )
                boost, sub = edge_rank(relation, src, tgt)
                rel_l = relation.lower()
                priority = RELATION_PRIORITY.get(rel_l, DEFAULT_RELATION_PRIORITY)
                scored_lines.append(((boost, sub, depth, priority, rel_l), line))

                nxt = tgt if src == nid else src
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, depth + 1))

        scored_lines.sort(key=lambda item: item[0])
        return [line for _, line in scored_lines]

    def format_boost(
        self,
        walk_lines: list[str],
        fact_hits: list[dict],
        *,
        query: str,
        max_items: int,
        max_item_chars: int,
        max_boost_chars: int,
        min_similarity: float,
    ) -> Optional[str]:
        from src.core.intelligence.knowledge_context_format import compact_knowledge_item

        bullets: list[str] = []
        seen: set[str] = set()
        total = 0

        def _try_add(text: str) -> bool:
            nonlocal total
            compact = compact_knowledge_item(
                text, max_chars=max_item_chars, query=query
            )
            if not compact or compact in seen:
                return True
            bullet = f"- {compact}"
            extra = len(bullet) + (1 if bullets else 0)
            if len(bullets) >= max_items:
                return False
            if total + extra > max_boost_chars:
                return False
            seen.add(compact)
            bullets.append(bullet)
            total += extra
            return True

        # Leave room for prose facts — walk alone was crowding out school/routine.
        walk_cap = max_items if not fact_hits else max(2, max_items // 2)
        for line in walk_lines[:walk_cap]:
            if not _try_add(line):
                break

        for hit in fact_hits:
            if len(bullets) >= max_items or total >= max_boost_chars:
                break
            similarity = float(hit.get("similarity", 0.0) or 0.0)
            if min_similarity > 0 and similarity < min_similarity:
                continue
            if not _try_add(hit.get("content") or ""):
                break

        # If facts didn't fill the budget, use remaining walk lines.
        if len(bullets) < max_items:
            for line in walk_lines[walk_cap:]:
                if not _try_add(line):
                    break

        return "\n".join(bullets) if bullets else None

    def score_nodes(self, query: str, nodes: list) -> list:
        """Score nodes by embedding similarity, falling back to keyword overlap."""
        query_emb = self._embed(query)
        q = query.lower()
        q_words = set(re.findall(r"[a-z0-9]+", q))
        # Topic name (the user's ## section heading) -> words that mean that topic.
        # Generic English only: a specific employer or project name here would be
        # dead weight on every other graph, and it is redundant anyway — naming a
        # thing already matches the fact containing it by embedding/keyword.
        topic_hints = {
            "school": {"graduate", "graduation", "bachelor", "master", "masters", "school",
                       "degree", "college", "university", "campus"},
            # Bare "work" is too broad — it matches "workout" and "how X works",
            # which floored every career fact onto technical questions.
            "career": {"job", "intern", "internship", "employer", "employee",
                       "coworker", "workplace"},
            "routine": {"routine", "schedule", "weekday", "wake", "morning", "summer"},
            "wedding": {"wedding", "venue", "married", "ceremony", "fiancée", "fiancee"},
            "projects": {"project", "projects", "youtube", "channel"},
            "family": {"family", "fiancée", "fiancee", "parent", "brother", "sister"},
        }

        scored = []
        for nid, data in nodes:
            content = data.get("content", "")
            score = 0.0
            if query_emb is not None:
                node_emb = data.get("_embedding")
                if node_emb is None:
                    node_emb = self._embed(content)
                    if node_emb:
                        self._graph.nodes[nid]["_embedding"] = node_emb
                if node_emb:
                    score = _cosine_similarity(query_emb, node_emb)

            if score == 0.0:
                # Keyword fallback (also fills when embeddings unavailable)
                c_words = set(re.findall(r"[a-z0-9]+", content.lower()))
                score = len(q_words & c_words) / max(len(q_words), 1)

            topic = (data.get("topic") or "").lower()
            if topic in topic_hints and (topic_hints[topic] & q_words):
                # Multiply, don't floor. `max(score, 0.55) + 0.15` pinned every
                # topic-matched fact to a flat 0.70 — above any usable threshold and
                # identical to its neighbours, so a single stray keyword forced a
                # whole topic into the prompt with its internal ranking destroyed.
                score *= TOPIC_HINT_BOOST

            scored.append((nid, data, min(score, 1.0)))

        return scored


def _cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two equal-length vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)
