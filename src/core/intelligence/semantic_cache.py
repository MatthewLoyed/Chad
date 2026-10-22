"""SemanticResponseCache — near-repeat query cache for side-effect-free turns (#516).

Layer above the prefix cache (#511, already handled by LM Studio/the adapter):
catches exact or semantically-similar repeat questions within a session (e.g.
"what's on my calendar today" asked twice, or rephrased) and returns the prior
answer with zero inference.

Scoped deliberately narrow for correctness:
- Per-session only — never serves an answer across conversations (see the
  session-isolation contract, issue #269).
- Only stores turns the caller confirms were side-effect-free (zero tool
  calls this turn) — a turn that ran a tool may have real-world effects or a
  result that's now stale, so it is never cached.
- Short TTL (minutes, not hours) — correctness risk from a stale semantic
  match is higher than the prefix-cache layer, so entries expire fast.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.shared.logger import setup_logger

logger = setup_logger("SemanticResponseCache")


@dataclass
class _CacheEntry:
    query: str
    embedding: List[float]
    response: str
    timestamp: float


class SemanticResponseCache:
    """Per-session, short-TTL cache of Q -> A pairs matched by embedding similarity."""

    def __init__(
        self,
        embedding_provider: Optional[object] = None,
        similarity_threshold: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        max_entries_per_session: int = 20,
    ):
        from src.shared.config_manager import ConfigManager

        self._embedding_provider = embedding_provider
        self._similarity_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else float(ConfigManager.get("cache.semantic_similarity_threshold", 0.95))
        )
        self._ttl_seconds = (
            ttl_seconds
            if ttl_seconds is not None
            else float(ConfigManager.get("cache.semantic_ttl_seconds", 300))
        )
        self._max_entries_per_session = max_entries_per_session
        self._sessions: Dict[str, List[_CacheEntry]] = {}

    def _get_embedding_provider(self):
        if self._embedding_provider is None:
            from src.core.intelligence.embedding_provider import EmbeddingProvider
            # Local (Nomic) — same embedder RAG retrieval already uses; no cloud
            # round-trip and no risk of leaking query text off-box for a cache check.
            self._embedding_provider = EmbeddingProvider(embedding_strategy="local")
        return self._embedding_provider

    def _embed(self, text: str) -> Optional[List[float]]:
        try:
            return self._get_embedding_provider().get_embedding(text)
        except Exception as e:
            logger.debug(f"[ SEMANTIC_CACHE ] Embedding failed (non-fatal): {e}")
            return None

    def _prune(self, session_id: str) -> None:
        entries = self._sessions.get(session_id)
        if not entries:
            return
        now = time.time()
        fresh = [e for e in entries if now - e.timestamp <= self._ttl_seconds]
        if fresh:
            self._sessions[session_id] = fresh[-self._max_entries_per_session:]
        else:
            del self._sessions[session_id]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def lookup(self, session_id: str, query: str) -> Optional[str]:
        """Return a cached answer for a near-duplicate query in this session, or None."""
        if not query or not query.strip():
            return None
        self._prune(session_id)
        entries = self._sessions.get(session_id)
        if not entries:
            return None
        embedding = self._embed(query)
        if not embedding:
            return None
        best_entry, best_score = None, 0.0
        for entry in entries:
            score = self._cosine(embedding, entry.embedding)
            if score > best_score:
                best_entry, best_score = entry, score
        if best_entry is not None and best_score >= self._similarity_threshold:
            logger.info(
                f"[ SEMANTIC_CACHE ] Hit (score={best_score:.3f}) session={session_id!r}: "
                f"{query!r} ~= {best_entry.query!r}"
            )
            return best_entry.response
        return None

    def store(self, session_id: str, query: str, response: str) -> None:
        """Record a side-effect-free Q->A pair for near-repeat matching. Caller is
        responsible for confirming the turn ran zero tools before calling this."""
        if not query or not query.strip() or not response or not response.strip():
            return
        embedding = self._embed(query)
        if not embedding:
            return
        self._prune(session_id)
        entries = self._sessions.setdefault(session_id, [])
        entries.append(
            _CacheEntry(query=query, embedding=embedding, response=response, timestamp=time.time())
        )
        if len(entries) > self._max_entries_per_session:
            del entries[: len(entries) - self._max_entries_per_session]

    def clear(self, session_id: Optional[str] = None) -> int:
        """Clear cached entries, scoped to one session if given, else all sessions."""
        if session_id is not None:
            return len(self._sessions.pop(session_id, []))
        count = sum(len(v) for v in self._sessions.values())
        self._sessions.clear()
        return count
