"""
Parse config/personal_knowledge.md into fact sections + optional ## _graph triples.

Fact sections (any ## heading except the reserved `_`-prefixed ones) become
kind=fact nodes. The ## _graph block declares entities and typed edges for MVP A1
(#608). The ## _core_profile block is the always-on identity block — it is NOT a
retrievable fact; it is injected into every user-facing system prompt verbatim.

Entity line:  Person:Sam
Edge line:    Sam -partner_of-> Alex
              Person:Sam -partner_of-> Person:Alex
              Document:budget.xlsx -about-> Event:Wedding2026
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

ENTITY_TYPES = frozenset({
    "Person",
    "Project",
    "Place",
    "Document",
    "Event",
    "Org",
    "Goal",
})

# Canonical relations from docs/research/TYPED_GRAPH_MEMORY.md — unknown
# snake_case relations are still accepted for forward compatibility.
# Exported for walk/retrieval (#609) and docs/tests.
KNOWN_RELATIONS = frozenset({
    "partner_of",
    "parent_of",
    "child_of",
    "sibling_of",
    "friend_of",
    "works_with",
    "owns",
    "member_of",
    "employed_by",
    "related_to_project",
    "lives_in",
    "located_at",
    "attended",
    "happened_at",
    "occurs_on",
    "about",
    "mentions",
    "sourced_from",
    "in_service_of",
    "blocked_by",
    "caused_by",
    "motivated_by",
    "related",
})

# Relations where the user can only have ONE live edge at a time, so asserting a
# new one supersedes the old rather than sitting alongside it (#656). Everything
# not listed here is multi-valued and never auto-expires anything.
#
# The list is deliberately tiny, because the two failure modes are wildly
# asymmetric: keeping a stale edge costs one competing walk line, while wrongly
# expiring a live one makes a true fact vanish from retrieval. So a relation
# earns a place here only when a second simultaneous value is genuinely
# incoherent rather than merely uncommon:
#   employed_by  one current employer. NOT `member_of` -- that covers clubs,
#                teams and gyms too, where "Alex joined the climbing gym" must
#                not expire their job. That ambiguity is exactly why employment
#                gets its own relation instead of overloading `member_of`.
#   lives_in     one current primary residence.
#   partner_of   one current partner.
# Deliberately excluded: parent_of/child_of/sibling_of/friend_of (many, and
# permanent), owns/member_of/related_to_project/works_with (many by nature),
# attended (many schools, and historical by definition).
SINGLE_VALUED_RELATIONS = frozenset({
    "employed_by",
    "lives_in",
    "partner_of",
})

__all__ = [
    "ENTITY_TYPES",
    "KNOWN_RELATIONS",
    "SINGLE_VALUED_RELATIONS",
    "GraphEntity",
    "GraphEdge",
    "PersonalKnowledge",
    "RuntimeRelation",
    "entity_node_id",
    "parse_personal_knowledge",
    "parse_graph_block",
    "split_section_facts",
    "is_placeholder_text",
    "extract_runtime_relations",
]

_ENTITY_LINE = re.compile(
    r"^\s*[-*]?\s*(?P<etype>\w+):(?P<name>[^\s(]+)"
    r"(?:\s*\((?P<attrs>[^)]*)\))?\s*$"
)
_EDGE_LINE = re.compile(
    r"^\s*[-*]?\s*"
    r"(?:(?P<src_type>\w+):)?(?P<src>[^\s]+)"
    r"\s+-(?P<rel>\w+)->\s+"
    r"(?:(?P<tgt_type>\w+):)?(?P<tgt>[^\s(]+)"
    r"(?:\s*\((?P<validity>[^)]*)\))?\s*$"
)
# Optional validity clause on an edge line, e.g.
#   Alex -employed_by-> Org:Tidewater (since 2023-06, until 2025-03)
# Both halves are independent and optional; a bare `(until X)` is the common
# case (a job that ended). Values are opaque strings -- see GraphEdge.
_EDGE_SINCE = re.compile(r"\bsince\s+([^,)]+)", re.IGNORECASE)
_EDGE_UNTIL = re.compile(r"\buntil\s+([^,)]+)", re.IGNORECASE)


@dataclass
class GraphEntity:
    id: str
    entity_type: str
    name: str
    # Free-text detail rendered inline on walk lines (#659): without it an entity
    # node stores only its own name, so `Alex -attended-> BSGraduation` reaches the
    # model carrying no date, degree or institution — a line it cannot act on, and
    # one that still costs a slot in the identity budget. Deliberately unstructured
    # prose rather than typed fields: the model reads it, nothing parses it, and
    # "reason free, constrain late" applies to what we store as much as to what the
    # model emits.
    attributes: Optional[str] = None


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    relation: str
    # Bi-temporal *valid time* -- when the relation was true in the world, as
    # opposed to when we ingested it (#656, following Zep/Graphiti
    # arXiv:2501.13956). `valid_to` set means the edge is history: excluded from
    # the normal walk, still reachable by an explicitly historical query.
    # Free-form strings ("2025-03", an ISO timestamp) -- only emptiness is ever
    # tested, never ordering, so no date parsing is needed or wanted here.
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


@dataclass
class PersonalKnowledge:
    facts: list[dict] = field(default_factory=list)
    entities: list[GraphEntity] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    core_profile: Optional[str] = None
    behavior: Optional[str] = None


def is_placeholder_text(content: str) -> bool:
    """True for unfilled personal_knowledge.md.example scaffold text.

    A fully-bracketed line is scaffold regardless of wording — that shape is what
    the template uses and is vanishingly rare in a real fact. The keyword list
    below then catches multi-line scaffold blocks (`## _core_profile`) whose later
    lines don't carry their own brackets.
    """
    s = (content or "").strip()
    if not s.startswith("["):
        return False
    if s.endswith("]") and "\n" not in s:
        return True
    low = s.lower()
    return any(
        k in low
        for k in (
            "fill in",
            "describe your",
            "your name",
            "any health context",
            "short-term and long-term",
            "financial context, if you want",
            "your pc specs",
            "hobbies, media",
            "active side projects",
            "daily schedule",
            "the handful of facts",
            "standing directives",
        )
    )


def entity_node_id(entity_type: str, name: str) -> str:
    return f"{entity_type}:{name}"


def name_tokens(blob: str) -> set[str]:
    """Tokenize an entity id/name, splitting CamelCase (WeddingRoadmap -> wedding, roadmap).

    The whole-string form is kept alongside the split parts so that a graph
    written as `HarborDynamics` and a fact saying "Harbor Dynamics" resolve to
    each other. That equivalence is load-bearing for #656: a failed entity
    resolution looks like a NEW value for a single-valued relation, which would
    wrongly expire the live edge -- the expensive failure direction.
    """
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", blob or "")
    tokens = {p.lower() for p in parts if len(p) >= 2}
    tokens |= set(re.findall(r"[a-z0-9]+", (blob or "").lower()))
    return tokens


@dataclass
class RuntimeRelation:
    """A typed relation deterministically extracted from a runtime-saved fact (#657).

    `source_is_ego` says which end of the edge is the user: True means the edge
    is written ego -relation-> entity (e.g. the user owns a pet, works at a
    company); False means entity -relation-> ego (e.g. a person's partner or
    parent is the ego, matching the `Sam -partner_of-> Alex` convention used
    throughout ## _graph).
    """

    entity_type: str
    name: str
    relation: str
    source_is_ego: bool


# Deterministic, closed-vocabulary patterns for the phrasings a model actually
# saves via manage_user_memory(save) -- "X is my partner", "X is our dog". Each
# requires an explicit "my"/"our" so the relation is unambiguously about the
# user, and a capitalized name so it doesn't fire on pronouns ("she is my
# sister"). Anything outside this small closed set is NOT a bug: broader
# extraction needs real language understanding, which belongs in the offline
# sleep-cognify path (LLM-assisted, off the hot path), not here. See #657.
_PROPER_NAME = r"[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2}"

_RUNTIME_RELATION_PATTERNS: tuple[tuple[re.Pattern, str, str, bool], ...] = (
    (
        re.compile(
            rf"\b(?P<name>{_PROPER_NAME})\s+is\s+(?:my|our)\s+"
            r"(?:partner|fianc[ée]e?|husband|wife|spouse|boyfriend|girlfriend)\b",
            re.IGNORECASE,
        ),
        "Person", "partner_of", False,
    ),
    (
        re.compile(
            rf"\b(?P<name>{_PROPER_NAME})\s+is\s+my\s+(?:father|mother|dad|mom|papa|mama)\b",
            re.IGNORECASE,
        ),
        "Person", "parent_of", False,
    ),
    (
        re.compile(
            rf"\b(?P<name>{_PROPER_NAME})\s+is\s+my\s+(?:brother|sister|sibling)\b",
            re.IGNORECASE,
        ),
        "Person", "sibling_of", False,
    ),
    (
        re.compile(
            rf"\b(?P<name>{_PROPER_NAME})\s+is\s+my\s+(?:friend|best friend)\b",
            re.IGNORECASE,
        ),
        "Person", "friend_of", False,
    ),
    (
        re.compile(
            rf"\b(?P<name>{_PROPER_NAME})\s+is\s+(?:my|our)\s+"
            r"(?:dog|cat|pet|puppy|kitten|bird|horse)\b",
            re.IGNORECASE,
        ),
        "Pet", "owns", True,
    ),
    (
        re.compile(
            rf"\bworks?\s+(?:at|for)\s+(?P<name>{_PROPER_NAME})",
        ),
        # `employed_by`, not `member_of`: employment is single-valued and must
        # supersede a previous employer, while `member_of` covers clubs and
        # teams where a second membership is normal. See
        # SINGLE_VALUED_RELATIONS.
        "Org", "employed_by", True,
    ),
    (
        re.compile(
            rf"\blives?\s+in\s+(?P<name>{_PROPER_NAME})",
        ),
        "Place", "lives_in", True,
    ),
)


def extract_runtime_relations(content: str) -> list[RuntimeRelation]:
    """Deterministically pull typed relations out of a single accepted fact.

    Best-effort and closed-vocabulary by design (see module comment above the
    pattern table) -- returns an empty list rather than guessing when a fact
    doesn't match one of the known shapes.
    """
    if not content or not content.strip():
        return []

    out: list[RuntimeRelation] = []
    for pattern, entity_type, relation, source_is_ego in _RUNTIME_RELATION_PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        name = match.group("name").strip().rstrip(".,;:")
        if not name:
            continue
        out.append(RuntimeRelation(entity_type, name, relation, source_is_ego))
    return out


def parse_personal_knowledge(text: str) -> PersonalKnowledge:
    """Parse full personal_knowledge.md text into facts + optional _graph."""
    result = PersonalKnowledge()
    if not text or not text.strip():
        return result

    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    for section in sections:
        if not section.strip() or section.startswith("#"):
            continue
        lines = section.strip().splitlines()
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        topic = heading.lower().replace(" ", "_")

        if topic == "_graph":
            entities, edges = parse_graph_block(body)
            result.entities.extend(entities)
            result.edges.extend(edges)
            continue

        if topic == "_core_profile":
            # Always-on identity block, never a retrievable fact node.
            result.core_profile = body or None
            continue

        if topic == "_behavior":
            # Directives about how Chad should act. Always on, never retrievable —
            # as fact nodes these matched weakly against everything and leaked into
            # unrelated turns ("prefer recent items" surfacing on a traceback).
            result.behavior = body or None
            continue

        if body:
            result.facts.extend(split_section_facts(topic, body))

    return result


def split_section_facts(topic: str, body: str) -> list[dict]:
    """One fact per line, not one blob per section.

    A whole `## family` section stored as a single node was retrieved as a unit and
    then compacted to `knowledge_graph.max_item_chars` — so the fact you needed was
    whichever sentence the truncator happened to pick. Splitting per line makes each
    fact short enough to survive intact and scored on its own merits.
    """
    lines = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line[:2] in ("- ", "* "):
            line = line[2:].strip()
        if line:
            lines.append(line)

    if not lines:
        return []
    if len(lines) == 1:
        return [{"id": topic, "topic": topic, "content": lines[0]}]
    return [
        {"id": f"{topic}#{i}", "topic": topic, "content": line}
        for i, line in enumerate(lines, start=1)
    ]


def parse_graph_block(body: str) -> tuple[list[GraphEntity], list[GraphEdge]]:
    """Parse the body of a ## _graph section."""
    entities: list[GraphEntity] = []
    edges: list[GraphEdge] = []
    by_name: dict[str, str] = {}  # lowercase name -> entity id
    seen_ids: set[str] = set()

    def ensure_entity(
        etype: Optional[str], name: str, attrs: Optional[str] = None
    ) -> Optional[str]:
        name = name.strip()
        if not name:
            return None
        if etype:
            etype = etype.strip()
            if etype not in ENTITY_TYPES:
                # Still accept — forward-compatible types
                pass
            nid = entity_node_id(etype, name)
            if nid not in seen_ids:
                entities.append(GraphEntity(id=nid, entity_type=etype, name=name, attributes=attrs))
                seen_ids.add(nid)
                by_name.setdefault(name.lower(), nid)
            elif attrs:
                # An edge line mentioning the entity may have created it before its
                # own declaration line was reached — attach the attributes now
                # rather than losing them to declaration order.
                for existing in entities:
                    if existing.id == nid and not existing.attributes:
                        existing.attributes = attrs
                        break
            return nid
        # Bare name: resolve against declared entities
        resolved = by_name.get(name.lower())
        if resolved:
            return resolved
        # Already a Type:Name token used as bare id
        if ":" in name:
            et, _, nm = name.partition(":")
            if et and nm:
                return ensure_entity(et, nm)
        return None

    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        edge_m = _EDGE_LINE.match(line)
        if edge_m:
            rel = edge_m.group("rel").strip()
            src_id = ensure_entity(edge_m.group("src_type"), edge_m.group("src"))
            tgt_id = ensure_entity(edge_m.group("tgt_type"), edge_m.group("tgt"))
            if src_id and tgt_id and rel:
                validity = edge_m.group("validity") or ""
                since = _EDGE_SINCE.search(validity)
                until = _EDGE_UNTIL.search(validity)
                edges.append(GraphEdge(
                    source_id=src_id,
                    target_id=tgt_id,
                    relation=rel,
                    valid_from=since.group(1).strip() if since else None,
                    valid_to=until.group(1).strip() if until else None,
                ))
            continue

        ent_m = _ENTITY_LINE.match(line)
        if ent_m:
            attrs = (ent_m.group("attrs") or "").strip() or None
            ensure_entity(ent_m.group("etype"), ent_m.group("name"), attrs)

    return entities, edges
