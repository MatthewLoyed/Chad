"""Regression tests for the two memory failures found in the July 2026 audit.

1. Relationship edges losing to an alphabetical tiebreak, so naming a person by
   name (rather than by role) failed to surface the edge connecting them to the
   user. Symptom: Chad asked what stage a relationship was in while holding the
   engagement and the wedding date in its graph.
2. The always-on core profile being written but never read on the wake path.

Both run offline — this is pure ranking and prompt assembly, no model calls.
See docs/architecture/MEMORY_AUDIT_2026-07.md.
"""

from __future__ import annotations

import pytest

from src.core.intelligence import graph_rag_client as graph_rag_client_module
from src.core.intelligence.graph_rag_client import GraphRAGClient
from src.core.intelligence.graph_retrieval import GraphRetriever
from src.core.intelligence.personal_knowledge_parse import (
    GraphEdge,
    GraphEntity,
    parse_personal_knowledge,
)
from src.core.intelligence.prompt_builder import SystemPromptBuilder

EGO = "Person:Alex"

ENTITIES = [
    GraphEntity(id="Person:Alex", entity_type="Person", name="Alex"),
    GraphEntity(id="Person:Sam", entity_type="Person", name="Sam"),
    GraphEntity(id="Person:Jordan", entity_type="Person", name="Jordan"),
    GraphEntity(id="Person:Riley", entity_type="Person", name="Riley"),
    GraphEntity(id="Event:Graduation", entity_type="Event", name="Graduation"),
    GraphEntity(id="Org:StateU", entity_type="Org", name="StateU"),
]

# `attended` / `best_man_of` / `friend_of` all sort BEFORE `partner_of`
# alphabetically — that is exactly what buried the relationship before.
EDGES = [
    GraphEdge(source_id="Person:Sam", target_id="Person:Alex", relation="partner_of"),
    GraphEdge(source_id="Person:Alex", target_id="Event:Graduation", relation="attended"),
    GraphEdge(source_id="Person:Jordan", target_id="Person:Alex", relation="best_man_of"),
    GraphEdge(source_id="Person:Jordan", target_id="Person:Alex", relation="friend_of"),
    GraphEdge(source_id="Person:Riley", target_id="Person:Alex", relation="friend_of"),
    GraphEdge(source_id="Person:Alex", target_id="Org:StateU", relation="member_of"),
]

FACTS = [
    {"id": "family", "topic": "family", "content": "Partner is Sam Example (wedding May 29, 2027)."},
    {"id": "school", "topic": "school", "content": "Graduated StateU in 2026."},
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A GraphRAGClient over a synthetic graph, isolated from the real config."""
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: (FACTS, ENTITIES, EDGES, None),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", FACTS)
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", ENTITIES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", EDGES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_CORE_PROFILE", None)

    from src.shared.config_manager import ConfigManager

    real_get = ConfigManager.get
    overrides = {
        "knowledge_graph.graph_path": str(tmp_path / "graph.json"),
        "knowledge_graph.ego_node_id": EGO,
    }
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: overrides.get(key, real_get(key, default))),
    )

    c = GraphRAGClient()
    import asyncio

    asyncio.run(c.initialize())
    return c


@pytest.mark.parametrize(
    "query",
    [
        "plan me a date with Sam, include places, prices, dessert, outfits",
        "what should I get Sam for their birthday",
        "who is Sam",
    ],
)
def test_partner_edge_survives_when_partner_named_not_role_named(client, query):
    """Naming the partner must surface the partner edge, not graduations."""
    lines = client.retrieval.collect_walk_lines(query, max_hops=2)
    assert lines, "walk produced no lines at all"
    assert "partner_of" in lines[0], (
        f"partner edge did not rank first for {query!r}; got {lines[:3]}"
    )


def test_partner_edge_beats_alphabetically_earlier_relations(client):
    """`attended`/`best_man_of`/`friend_of` must not outrank `partner_of`."""
    lines = client.retrieval.collect_walk_lines("plan a date with Sam", max_hops=2)
    partner_at = next(i for i, l in enumerate(lines) if "partner_of" in l)
    for relation in ("attended", "best_man_of", "friend_of", "member_of"):
        other_at = next((i for i, l in enumerate(lines) if f"-{relation}->" in l), None)
        if other_at is not None:
            assert partner_at < other_at, f"{relation} outranked partner_of"


def test_ego_node_seeded_alongside_another_match(client):
    """Naming another person must still pull in the user's own edges."""
    seeds = client.retrieval.seed_entity_ids("plan a date with Sam")
    assert "Person:Sam" in seeds
    assert EGO in seeds, "ego node was not seeded, so relationship edges are unreachable"


@pytest.mark.parametrize(
    "query",
    [
        "fix this python traceback in my websocket handler",
        "what is the weather tomorrow",
        "refactor the tool selector to use a registry",
    ],
)
def test_impersonal_queries_get_no_walk_lines(client, query):
    """Ego seeding must not walk the user's family tree into unrelated turns.

    Seeding ego unconditionally injected `partner_of`/`parent_of`/`sibling_of`
    triples into every coding and weather question — the fix for one recall bug
    creating a context-pollution bug.
    """
    assert client.retrieval.seed_entity_ids(query) == []
    assert client.retrieval.collect_walk_lines(query, max_hops=2) == []


def test_ego_auto_detects_most_connected_person(client, monkeypatch):
    """No real name should be hardcoded in tracked source to make this work."""
    from src.shared.config_manager import ConfigManager

    real_get = ConfigManager.get
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(
            lambda key, default=None: None
            if key == "knowledge_graph.ego_node_id"
            else real_get(key, default)
        ),
    )
    client.retrieval.invalidate_caches()
    assert client.retrieval.ego_node_id() == EGO


def test_partner_edge_reaches_the_rendered_boost_block(client):
    """End to end: the budgeted, truncated output actually contains the fact."""
    import asyncio

    boost = asyncio.run(client.search("plan me a date with Sam"))
    assert boost and "partner_of" in boost, f"partner edge missing from boost: {boost!r}"


# ---------------------------------------------------------------------------
# Category seeding generalizes across graphs (#661)
# ---------------------------------------------------------------------------

CATEGORY_ENTITIES = [
    GraphEntity(id="Person:Alex", entity_type="Person", name="Alex"),
    GraphEntity(id="Project:Zephyr", entity_type="Project", name="Zephyr"),
    GraphEntity(id="Project:Lumen", entity_type="Project", name="Lumen"),
    GraphEntity(id="Document:LeaseAgreement", entity_type="Document", name="LeaseAgreement"),
    GraphEntity(id="Org:Acme", entity_type="Org", name="Acme"),
]

CATEGORY_EDGES = [
    GraphEdge(source_id="Person:Alex", target_id="Project:Zephyr", relation="owns"),
    GraphEdge(source_id="Person:Alex", target_id="Project:Lumen", relation="owns"),
    GraphEdge(source_id="Document:LeaseAgreement", target_id="Person:Alex", relation="about"),
]


@pytest.fixture
def category_client(monkeypatch, tmp_path):
    """Entity names deliberately share nothing with the real user's graph."""
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: ([], CATEGORY_ENTITIES, CATEGORY_EDGES, None),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", CATEGORY_ENTITIES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", CATEGORY_EDGES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_CORE_PROFILE", None)

    from src.shared.config_manager import ConfigManager

    real_get = ConfigManager.get
    overrides = {
        "knowledge_graph.graph_path": str(tmp_path / "category.json"),
        "knowledge_graph.ego_node_id": EGO,
    }
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: overrides.get(key, real_get(key, default))),
    )

    import asyncio

    c = GraphRAGClient()
    asyncio.run(c.initialize())
    return c


def test_category_word_seeds_entities_of_that_type(category_client):
    """'projects' must reach Project entities on a graph it was never tuned against."""
    seeds = category_client.retrieval.seed_entity_ids("remind me what my projects are")
    assert "Project:Zephyr" in seeds
    assert "Project:Lumen" in seeds


def test_category_seeding_respects_the_entity_type(category_client):
    """A document query must not drag in unrelated Projects."""
    seeds = category_client.retrieval.seed_entity_ids("what documents do i have")
    assert "Document:LeaseAgreement" in seeds
    assert not any(s.startswith("Project:") for s in seeds)


def test_category_seeding_is_capped(category_client, monkeypatch):
    """A large catalog must not flood the seed list."""
    from src.core.intelligence import graph_retrieval

    many = [
        GraphEntity(id=f"Document:Doc{i}", entity_type="Document", name=f"Doc{i}")
        for i in range(20)
    ]
    for entity in many:
        category_client._upsert_entity_node(entity, created_at="now", source="test")

    seeds = category_client.retrieval.seed_entity_ids("what documents do i have")
    documents = [s for s in seeds if s.startswith("Document:")]
    assert len(documents) <= graph_retrieval._CATEGORY_SEED_CAP


@pytest.mark.parametrize(
    "query",
    [
        # Coding vocabulary that must NOT be read as a personal category query.
        "what files changed in the last commit",
        "update the docs for this module",
    ],
)
def test_coding_vocabulary_does_not_seed_a_category(category_client, query):
    assert category_client.retrieval.seed_entity_ids(query) == []


# ---------------------------------------------------------------------------
# Entity attributes on walk lines (#659)
# ---------------------------------------------------------------------------

ATTR_ENTITIES = [
    GraphEntity(id="Person:Alex", entity_type="Person", name="Alex", attributes="the user"),
    GraphEntity(id="Person:Sam", entity_type="Person", name="Sam", attributes="marine biologist"),
    GraphEntity(id="Org:StateU", entity_type="Org", name="StateU", attributes="in Corvallis"),
    GraphEntity(id="Event:Graduation", entity_type="Event", name="Graduation"),
]

ATTR_EDGES = [
    GraphEdge(source_id="Person:Sam", target_id="Person:Alex", relation="partner_of"),
    GraphEdge(source_id="Person:Alex", target_id="Org:StateU", relation="attended"),
    GraphEdge(source_id="Person:Alex", target_id="Event:Graduation", relation="attended"),
    GraphEdge(
        source_id="Person:Alex", target_id="Org:StateU", relation="employed_by",
        valid_to="2026-02-01T00:00:00+00:00",
    ),
]


@pytest.fixture
def attr_client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: ([], ATTR_ENTITIES, ATTR_EDGES, None),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", ATTR_ENTITIES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", ATTR_EDGES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_CORE_PROFILE", None)

    from src.shared.config_manager import ConfigManager

    real_get = ConfigManager.get
    overrides = {
        "knowledge_graph.graph_path": str(tmp_path / "attrs.json"),
        "knowledge_graph.ego_node_id": EGO,
    }
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: overrides.get(key, real_get(key, default))),
    )

    import asyncio

    c = GraphRAGClient()
    asyncio.run(c.initialize())
    return c


def test_attributes_render_inline_on_the_walk_line(attr_client):
    assert attr_client.retrieval.annotated_label("Org:StateU") == "StateU (in Corvallis)"


def test_ego_attributes_are_suppressed(attr_client):
    """The user's own details are already in the always-on core profile."""
    assert attr_client.retrieval.annotated_label(EGO) == "Alex"


def test_entity_without_attributes_renders_byte_identically(attr_client):
    assert attr_client.retrieval.annotated_label("Event:Graduation") == "Graduation"


def test_attributes_and_edge_validity_share_one_parenthetical(attr_client):
    """An attributed, expired edge must not trail two separate bracketed groups."""
    lines = attr_client.retrieval.collect_walk_lines(
        "where did i used to work", max_hops=2
    )
    expired = [line for line in lines if "until" in line]
    assert expired, f"expired edge not surfaced on a historical query: {lines!r}"
    assert expired[0] == "Alex -employed_by-> StateU (in Corvallis, until 2026-02-01)"


def test_walk_line_carries_the_attribute_end_to_end(attr_client):
    lines = attr_client.retrieval.collect_walk_lines("what university did i attend", max_hops=2)
    assert any("StateU (in Corvallis)" in line for line in lines), lines


# ---------------------------------------------------------------------------
# Temporal edges + supersession (#656)
# ---------------------------------------------------------------------------

TEMPORAL_ENTITIES = [
    GraphEntity(id="Person:Alex", entity_type="Person", name="Alex"),
    GraphEntity(id="Person:Sam", entity_type="Person", name="Sam"),
    GraphEntity(id="Org:HarborDynamics", entity_type="Org", name="HarborDynamics"),
    GraphEntity(id="Place:Portland", entity_type="Place", name="Portland"),
]

TEMPORAL_EDGES = [
    GraphEdge(source_id="Person:Alex", target_id="Org:HarborDynamics", relation="employed_by"),
    GraphEdge(source_id="Person:Alex", target_id="Place:Portland", relation="lives_in"),
    GraphEdge(source_id="Person:Sam", target_id="Person:Alex", relation="partner_of"),
]


@pytest.fixture
def temporal_client(monkeypatch, tmp_path):
    """A client whose ego already has live single-valued edges to supersede."""
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: ([], TEMPORAL_ENTITIES, TEMPORAL_EDGES, None),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", TEMPORAL_ENTITIES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", TEMPORAL_EDGES)
    monkeypatch.setattr(graph_rag_client_module, "SEED_CORE_PROFILE", None)

    from src.shared.config_manager import ConfigManager

    real_get = ConfigManager.get
    overrides = {
        "knowledge_graph.graph_path": str(tmp_path / "temporal.json"),
        "knowledge_graph.ego_node_id": EGO,
    }
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: overrides.get(key, real_get(key, default))),
    )

    import asyncio

    c = GraphRAGClient()
    asyncio.run(c.initialize())
    return c


@pytest.mark.asyncio
async def test_new_employer_expires_the_old_edge(temporal_client):
    """The measured blind spot: 0.43 similarity, invisible to embeddings, caught by cardinality."""
    client = temporal_client
    await client.ingest("Alex now works at Redwing Marine.", source="manual")

    old = client._graph.edges["Person:Alex", "Org:HarborDynamics", "employed_by"]
    assert old["valid_to"], "old employer edge was not expired"
    assert client._graph.has_edge("Person:Alex", "Org:Redwing Marine", key="employed_by")
    assert not client._graph.edges[
        "Person:Alex", "Org:Redwing Marine", "employed_by"
    ]["valid_to"]


@pytest.mark.asyncio
async def test_expired_edge_is_hidden_from_present_tense_queries(temporal_client):
    """The regression that matters: history must not leak into 'where do I work'."""
    client = temporal_client
    await client.ingest("Alex now works at Redwing Marine.", source="manual")

    lines = client.retrieval.collect_walk_lines("where do i work", max_hops=2)
    assert any("Redwing" in l for l in lines), f"new employer missing: {lines!r}"
    assert not any("HarborDynamics" in l for l in lines), f"expired employer leaked: {lines!r}"


@pytest.mark.asyncio
async def test_expired_edge_is_reachable_from_a_historical_query(temporal_client):
    """...but an explicitly past-tense question can still get it, marked as ended."""
    client = temporal_client
    await client.ingest("Alex now works at Redwing Marine.", source="manual")

    lines = client.retrieval.collect_walk_lines("where did i used to work", max_hops=2)
    expired = [l for l in lines if "HarborDynamics" in l]
    assert expired, f"history unreachable even with a past-tense marker: {lines!r}"
    assert "until" in expired[0], f"expired edge rendered as if current: {expired[0]!r}"


@pytest.mark.asyncio
async def test_multi_valued_relation_never_expires_anything(temporal_client):
    """A club membership must not expire the user's job -- the expensive failure direction."""
    client = temporal_client
    await client.ingest("Alex is a member of the Cascade Climbing Club.", source="manual")

    assert not client._graph.edges[
        "Person:Alex", "Org:HarborDynamics", "employed_by"
    ]["valid_to"]


@pytest.mark.asyncio
async def test_restating_the_same_value_does_not_expire_it(temporal_client):
    """Re-asserting a single-valued relation must not expire-and-readd its own edge."""
    client = temporal_client
    await client.ingest("Alex works at Harbor Dynamics.", source="manual")

    assert not client._graph.edges[
        "Person:Alex", "Org:HarborDynamics", "employed_by"
    ]["valid_to"]


@pytest.mark.parametrize(
    "query",
    [
        # Past-tense cue + personal-life subject.
        "where did i used to work",
        "who was my previous employer",
        "where did i live previously",
        "what company did i formerly work for",
    ],
)
def test_history_intent_detected(query):
    assert GraphRetriever.wants_history(query) is True


@pytest.mark.parametrize(
    "query",
    [
        # A past-tense cue with NO personal-life subject is a coding question.
        # Every one of these matched a bare-cue check and would have seeded the
        # ego node into an unrelated turn.
        "rewrite my previous implementation of the tool selector",
        "revert to my former approach in this module",
        "refactor my regex-based parser",
        "fix my index-based lookup",
        "explain how tcp handshakes work",
    ],
)
def test_history_intent_not_triggered_by_code_talk(query):
    assert GraphRetriever.wants_history(query) is False


def test_history_query_does_not_seed_ego_without_a_life_subject(client):
    """The consequence of the above: no seed, so no walk lines, on a coding turn."""
    assert client.retrieval.seed_entity_ids("rewrite my previous implementation") == []


@pytest.mark.asyncio
async def test_supersession_is_reported_before_the_write(temporal_client):
    """describe_supersession is read-only — the gate calls it to explain, not to mutate."""
    client = temporal_client

    assert await client.describe_supersession("Alex now works at Redwing Marine.") == (
        "HarborDynamics"
    )
    assert not client._graph.edges[
        "Person:Alex", "Org:HarborDynamics", "employed_by"
    ]["valid_to"], "describe_supersession must not mutate the graph"
    assert await client.describe_supersession("Alex is allergic to shellfish.") is None


# ---------------------------------------------------------------------------
# Runtime saves join the typed graph (#657)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_save_creates_typed_entity_and_edge(client):
    """A save matching a recognized shape must create the entity + edge, not just a prose node."""
    await client.ingest("Nova is my dog.", source="manual")

    assert "Pet:Nova" in client._graph
    assert client._graph.has_edge(EGO, "Pet:Nova", key="owns")
    assert client._graph.edges[EGO, "Pet:Nova", "owns"]["confidence"] == "inferred"


@pytest.mark.asyncio
async def test_runtime_save_reachable_via_walk_not_only_similarity(client):
    """The whole point of #657: the fact must be reachable via the typed walk."""
    await client.ingest("Nova is my dog.", source="manual")

    lines = client.retrieval.collect_walk_lines("tell me about Nova", max_hops=2)
    assert any("owns" in l and "Nova" in l for l in lines), (
        f"runtime-saved pet not reachable via walk: {lines!r}"
    )


@pytest.mark.asyncio
async def test_runtime_save_resolves_full_name_to_existing_entity(client):
    """Saving 'Sam Okafor is my partner' must reuse Person:Sam, not create a second node."""
    before = {
        nid for nid, d in client._graph.nodes(data=True)
        if d.get("kind") == "entity" and d.get("entity_type") == "Person"
    }

    await client.ingest("Sam Okafor is my partner.", source="manual")

    after = {
        nid for nid, d in client._graph.nodes(data=True)
        if d.get("kind") == "entity" and d.get("entity_type") == "Person"
    }
    assert after == before, f"expected no new Person entity, got {after - before}"
    assert "Person:Sam Okafor" not in client._graph


@pytest.mark.asyncio
async def test_runtime_save_never_overwrites_a_curated_edge(client):
    """Curated (## _graph) edges always win -- a runtime save must not demote one to inferred."""
    await client.ingest("Sam is my partner.", source="manual")

    edge = client._graph.edges["Person:Sam", EGO, "partner_of"]
    assert edge["confidence"] == "curated"


@pytest.mark.asyncio
async def test_runtime_save_unrecognized_shape_stays_prose_only(client):
    """A fact that matches none of the closed-vocabulary patterns creates no entity."""
    entities_before = {nid for nid, d in client._graph.nodes(data=True) if d.get("kind") == "entity"}

    await client.ingest("The garage door code is 4417.", source="manual")

    entities_after = {nid for nid, d in client._graph.nodes(data=True) if d.get("kind") == "entity"}
    assert entities_after == entities_before


# ---------------------------------------------------------------------------
# Always-on core profile
# ---------------------------------------------------------------------------

CORE_MD = """# Personal knowledge

## _core_profile

Alex Example lives in Austin. Engaged to Sam; wedding May 29, 2027.

## identity

Alex Example is a software engineer.
"""


def test_core_profile_section_parses_and_is_not_a_fact():
    parsed = parse_personal_knowledge(CORE_MD)
    assert parsed.core_profile
    assert "Engaged to Sam" in parsed.core_profile
    assert "_core_profile" not in {f["id"] for f in parsed.facts}


def test_core_profile_syncs_from_markdown_and_reads_back_sync(monkeypatch, tmp_path):
    """The wake path (sync, no event loop) must be able to read the profile.

    This is the bug that made the block dead code: it was written by sleep-cognify
    and never read by prompt assembly.
    """
    parsed = parse_personal_knowledge(CORE_MD)
    monkeypatch.setattr(
        graph_rag_client_module,
        "_load_personal_knowledge_seed",
        lambda: ([], [], [], parsed.core_profile),
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_ENTITIES", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_EDGES", [])
    monkeypatch.setattr(graph_rag_client_module, "SEED_CORE_PROFILE", parsed.core_profile)

    from src.shared.config_manager import ConfigManager

    real_get = ConfigManager.get
    graph_path = str(tmp_path / "graph.json")
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(
            lambda key, default=None: graph_path
            if key == "knowledge_graph.graph_path"
            else real_get(key, default)
        ),
    )
    GraphRAGClient._CORE_PROFILE_CACHE = (None, None)

    import asyncio

    asyncio.run(GraphRAGClient().initialize())

    assert "Engaged to Sam" in (GraphRAGClient.read_core_profile_sync() or "")

    block = SystemPromptBuilder.core_profile_block()
    assert "### CORE PROFILE" in block
    assert "Engaged to Sam" in block


def test_core_profile_absent_yields_empty_block(monkeypatch):
    monkeypatch.setattr(
        GraphRAGClient, "read_core_profile_sync", staticmethod(lambda: None)
    )
    assert SystemPromptBuilder.core_profile_block() == ""


def test_sections_split_into_one_fact_per_line():
    """A section stored as one blob let truncation decide which fact survived."""
    parsed = parse_personal_knowledge(
        "## family\n\nFather is Marcus.\nMother is Elena.\nThey have a corgi named Biscuit.\n"
    )
    contents = [f["content"] for f in parsed.facts]
    assert contents == ["Father is Marcus.", "Mother is Elena.", "They have a corgi named Biscuit."]
    assert all(f["topic"] == "family" for f in parsed.facts)


def test_single_line_section_keeps_bare_topic_id():
    parsed = parse_personal_knowledge("## finances\n\nA scholarship covers tuition.\n")
    assert [f["id"] for f in parsed.facts] == ["finances"]


def test_behavior_section_is_not_a_retrievable_fact():
    parsed = parse_personal_knowledge(
        "## _behavior\n\nAsk with the tool, not in chat.\n\n## identity\n\nAlex is the user.\n"
    )
    assert parsed.behavior == "Ask with the tool, not in chat."
    assert all("Ask with the tool" not in f["content"] for f in parsed.facts)


def test_seed_sync_removes_facts_deleted_from_markdown(client, monkeypatch):
    """Editing personal_knowledge.md used to require deleting the whole graph."""
    assert any(
        (d.get("content") or "").startswith("Graduated StateU")
        for _, d in client._graph.nodes(data=True)
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [FACTS[0]])
    client._sync_seed_facts_from_markdown()
    assert not any(
        (d.get("content") or "").startswith("Graduated StateU")
        for _, d in client._graph.nodes(data=True)
    )


def test_seed_sync_does_not_resurrect_a_forgotten_fact(client, monkeypatch):
    """Re-seeding must respect forget() — invalid stays invalid, matched by content."""
    target = FACTS[1]["content"]
    for nid, data in client._graph.nodes(data=True):
        if (data.get("content") or "").strip() == target:
            data["valid"] = False
            client._graph.remove_node(nid)  # force the re-add path
            client._graph.add_node(
                nid, content=target, topic="school", source="seed", kind="fact", valid=False
            )
            break

    monkeypatch.setattr(
        graph_rag_client_module,
        "SEED_FACTS",
        [FACTS[0], {"id": "school", "topic": "school", "content": target}],
    )
    client._sync_seed_facts_from_markdown()

    revived = [
        d for _, d in client._graph.nodes(data=True)
        if (d.get("content") or "").strip() == target
    ]
    assert revived and all(d.get("valid") is False for d in revived)


def test_seed_sync_never_wipes_graph_when_markdown_missing(client, monkeypatch):
    before = client._graph.number_of_nodes()
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [])
    client._sync_seed_facts_from_markdown()
    assert client._graph.number_of_nodes() == before


def test_seed_sync_leaves_runtime_saved_facts_alone(client, monkeypatch):
    client._graph.add_node(
        "manual_1", content="Garage code is 1234.", topic="general",
        source="manual", kind="fact", valid=True,
    )
    monkeypatch.setattr(graph_rag_client_module, "SEED_FACTS", [FACTS[0]])
    client._sync_seed_facts_from_markdown()
    assert "manual_1" in client._graph


def test_topic_hint_boosts_rather_than_floors(client):
    """A flat floor pinned every topic-matched fact to an identical 0.70."""
    scores = [
        s for _, _, s in client.retrieval.score_nodes(
            "what is my family like", list(client._graph.nodes(data=True))
        )
    ]
    assert len(set(round(s, 3) for s in scores)) > 1, "scores collapsed to one value"
    assert not any(abs(s - 0.70) < 1e-9 for s in scores), "0.70 saturation is back"


def test_core_profile_only_injected_for_user_facing_scopes():
    scopes = SystemPromptBuilder._CORE_PROFILE_SCOPES
    assert {"supervisor", "heartbeat", "voice", "conversational"} <= scopes
    # Workers get the rest of the `always` persona stripped; don't re-add identity.
    assert not scopes & {"planner", "validator", "scout", "code_agent"}
