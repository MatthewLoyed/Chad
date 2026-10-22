"""
Domain-agent isolation tests.

These assert the *guarantee*, not the implementation: a domain agent sees its own facts
plus the untagged global tier, and never another domain's. Every retrieval path added
later should get a case here — a leak is silent at runtime (the model simply knows
something it shouldn't), so these tests are the only thing that catches it.
"""

import networkx as nx
import pytest

from src.core.intelligence.graph_retrieval import GraphRetriever
from src.shared.domain_scope import (
    matches_domain,
    metadata_matches_domain,
    normalize_domain,
    record_matches_domain,
)


class TestMatchesDomain:
    def test_untagged_is_visible_everywhere(self):
        """The global tier — who the user is — must reach every domain."""
        assert matches_domain(None, "financial")
        assert matches_domain(None, None)
        assert matches_domain(None, "fitness")

    def test_own_domain_is_visible(self):
        assert matches_domain("financial", "financial")

    def test_other_domain_is_hidden(self):
        assert not matches_domain("financial", "fitness")
        assert not matches_domain("fitness", "financial")

    def test_tagged_record_hidden_from_general_tier(self):
        """A general chat picking no domain must not see domain-scoped data."""
        assert not matches_domain("financial", None)

    def test_empty_string_is_not_a_domain(self):
        """'' must collapse to untagged, or a blank client field becomes a real scope."""
        assert normalize_domain("") is None
        assert normalize_domain("   ") is None
        assert matches_domain("", "financial")
        assert matches_domain("financial", "financial   ")

    def test_record_and_metadata_helpers_agree(self):
        assert record_matches_domain({"domain": "financial"}, "financial")
        assert not record_matches_domain({"domain": "financial"}, None)
        assert record_matches_domain({}, "financial")
        assert metadata_matches_domain({"domain": "financial"}, "financial")
        assert not metadata_matches_domain({"domain": "financial"}, "fitness")
        assert metadata_matches_domain(None, "financial")


def _no_embed(_text):
    """No embedding provider in tests — GraphRetriever falls back to keyword overlap,
    which is deterministic and enough to prove which nodes are *reachable*."""
    return None


def _graph_with_domains():
    """A graph holding one financial fact, one fitness fact, and one global fact."""
    g = nx.MultiDiGraph()
    g.add_node("f1", kind="fact", content="My brokerage is Fidelity",
               topic="finance", valid=True, domain="financial")
    g.add_node("f2", kind="fact", content="I bench press 185 pounds",
               topic="fitness", valid=True, domain="fitness")
    g.add_node("f3", kind="fact", content="My name is Alex",
               topic="profile", valid=True, domain=None)
    return g


class TestGraphFactScoping:
    @pytest.fixture
    def retriever(self):
        g = _graph_with_domains()
        return GraphRetriever(lambda: g, _no_embed)

    def _contents(self, hits):
        return {h["content"] for h in hits}

    def test_financial_turn_sees_own_and_global_only(self, retriever):
        hits = retriever.collect_fact_hits("brokerage Fidelity bench name",
                                           threshold=0.0, domain="financial")
        contents = self._contents(hits)
        assert "My brokerage is Fidelity" in contents
        assert "My name is Alex" in contents, "global tier must stay visible"
        assert "I bench press 185 pounds" not in contents, "cross-domain leak"

    def test_general_turn_sees_only_global(self, retriever):
        contents = self._contents(
            retriever.collect_fact_hits("brokerage bench name", threshold=0.0, domain=None)
        )
        assert contents == {"My name is Alex"}

    def test_other_domain_cannot_reach_financial_fact(self, retriever):
        contents = self._contents(
            retriever.collect_fact_hits("brokerage Fidelity", threshold=0.0, domain="fitness")
        )
        assert "My brokerage is Fidelity" not in contents


class TestGraphWalkScoping:
    """The typed-edge walk is a separate reachability path from fact similarity."""

    @pytest.fixture
    def retriever(self):
        g = _graph_with_domains()
        # An UNTAGGED entity that a general chat can legitimately seed on, wired to a
        # domain-scoped fact. Without endpoint scoping the walk emits the scoped node's
        # label through this untagged doorway.
        g.add_node("Org:Fidelity", kind="entity", entity_type="Org",
                   name="Fidelity", valid=True)
        g.add_node("Person:Alex", kind="entity", entity_type="Person",
                   name="Alex", valid=True)
        g.add_node("acct1", kind="fact", content="Brokerage balance is 42000",
                   topic="finance", valid=True, domain="financial")
        g.add_edge("Person:Alex", "acct1", relation="owns")
        g.add_edge("Person:Alex", "Org:Fidelity", relation="member_of")
        return GraphRetriever(lambda: g, _no_embed)

    def test_walk_does_not_cross_into_another_domain(self, retriever):
        lines = retriever.collect_walk_lines("Alex", max_hops=2, domain=None)
        joined = " ".join(lines)
        assert "acct1" not in joined, "walk reached a financial node from the general tier"

    def test_walk_reaches_own_domain(self, retriever):
        lines = retriever.collect_walk_lines("Alex", max_hops=2, domain="financial")
        assert any("acct1" in ln for ln in lines), "domain must reach its own scoped node"

    def test_seed_entities_respect_domain(self):
        g = nx.MultiDiGraph()
        g.add_node("Org:Vanguard", kind="entity", entity_type="Org",
                   name="Vanguard", valid=True, domain="financial")
        seeds_general = GraphRetriever(lambda: g, _no_embed).seed_entity_ids("vanguard", domain=None)
        seeds_fin = GraphRetriever(lambda: g, _no_embed).seed_entity_ids("vanguard", domain="financial")
        assert seeds_general == []
        assert "Org:Vanguard" in seeds_fin


class TestSearchCacheIsolation:
    """Regression guard for the highest-risk leak in this feature.

    GraphRAGClient._query_cache used to key on query text alone. With that key, asking the
    same question in a general chat immediately after a Financial chat returned the CACHED
    financial answer — isolation that passes every filter test and still leaks in practice.
    """

    @pytest.mark.asyncio
    async def test_same_query_different_domains_does_not_share_cache(self):
        from src.core.intelligence.graph_rag_client import GraphRAGClient

        client = GraphRAGClient.__new__(GraphRAGClient)  # bypass __init__/graph load
        client._is_initialized = True
        client._query_cache = {}

        calls = []

        class _Retrieval:
            def query(self, q, domain=None):
                calls.append(domain)
                return f"boost-for-{domain}"

        client.retrieval = _Retrieval()

        financial = await client.search("what do I have?", domain="financial")
        general = await client.search("what do I have?", domain=None)
        fitness = await client.search("what do I have?", domain="fitness")

        assert financial == "boost-for-financial"
        assert general == "boost-for-None", "general chat served the cached financial boost"
        assert fitness == "boost-for-fitness"
        assert calls == ["financial", None, "fitness"], "a domain was answered from cache"

        # ...and the cache still works WITHIN one domain.
        again = await client.search("what do I have?", domain="financial")
        assert again == "boost-for-financial"
        assert len(calls) == 3, "cache stopped working entirely"
