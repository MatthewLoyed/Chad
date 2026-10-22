"""Unit tests for SemanticResponseCache (#516)."""

import time

import pytest

from src.core.intelligence.semantic_cache import SemanticResponseCache


class _FakeEmbeddingProvider:
    """Deterministic embeddings so similarity behavior is testable without a real model."""

    def __init__(self, vectors):
        self._vectors = vectors

    def get_embedding(self, text):
        return self._vectors.get(text)


def _make_cache(vectors, **kwargs):
    provider = _FakeEmbeddingProvider(vectors)
    return SemanticResponseCache(
        embedding_provider=provider,
        similarity_threshold=kwargs.pop("similarity_threshold", 0.95),
        ttl_seconds=kwargs.pop("ttl_seconds", 300),
        **kwargs,
    )


def test_exact_repeat_hits():
    vectors = {"what's on my calendar today": [1.0, 0.0, 0.0]}
    cache = _make_cache(vectors)
    cache.store("session-a", "what's on my calendar today", "You have a 2pm meeting.")
    hit = cache.lookup("session-a", "what's on my calendar today")
    assert hit == "You have a 2pm meeting."


def test_near_paraphrase_hits_above_threshold():
    vectors = {
        "what's on my calendar today": [1.0, 0.0, 0.0],
        "what do I have on my calendar today?": [0.99, 0.14, 0.0],  # cos ~0.99
    }
    cache = _make_cache(vectors, similarity_threshold=0.95)
    cache.store("session-a", "what's on my calendar today", "You have a 2pm meeting.")
    hit = cache.lookup("session-a", "what do I have on my calendar today?")
    assert hit == "You have a 2pm meeting."


def test_dissimilar_query_misses():
    vectors = {
        "what's on my calendar today": [1.0, 0.0, 0.0],
        "what's the weather like": [0.0, 1.0, 0.0],  # orthogonal
    }
    cache = _make_cache(vectors, similarity_threshold=0.95)
    cache.store("session-a", "what's on my calendar today", "You have a 2pm meeting.")
    hit = cache.lookup("session-a", "what's the weather like")
    assert hit is None


def test_scoped_per_session():
    vectors = {"what's on my calendar today": [1.0, 0.0, 0.0]}
    cache = _make_cache(vectors)
    cache.store("session-a", "what's on my calendar today", "You have a 2pm meeting.")
    hit = cache.lookup("session-b", "what's on my calendar today")
    assert hit is None


def test_ttl_expiry():
    vectors = {"what's on my calendar today": [1.0, 0.0, 0.0]}
    cache = _make_cache(vectors, ttl_seconds=0.05)
    cache.store("session-a", "what's on my calendar today", "You have a 2pm meeting.")
    time.sleep(0.1)
    hit = cache.lookup("session-a", "what's on my calendar today")
    assert hit is None


def test_clear_session_only():
    vectors = {
        "q1": [1.0, 0.0],
        "q2": [0.0, 1.0],
    }
    cache = _make_cache(vectors)
    cache.store("session-a", "q1", "answer1")
    cache.store("session-b", "q2", "answer2")
    removed = cache.clear("session-a")
    assert removed == 1
    assert cache.lookup("session-a", "q1") is None
    assert cache.lookup("session-b", "q2") == "answer2"


def test_missing_embedding_never_raises():
    cache = _make_cache({})  # provider returns None for anything
    cache.store("session-a", "unknown query", "some answer")
    assert cache.lookup("session-a", "unknown query") is None
