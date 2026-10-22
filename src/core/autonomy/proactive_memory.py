"""
Proactive Memory Service - 2026-Backed Real-Time Agentic Memory Extraction

Inspired by Mem0, Zep, and Graphiti temporal memory extraction architectures:
1. Tier-1 Fast Heuristic Filter (<1ms regex, 0 token cost).
2. Tier-2 Model-Assisted Atomic Fact Distillation (Mem0 distillation-first pattern).
3. Tier-3 Temporal Gated Ingestion via MemoryWriteGate (deduplication & conflict resolution).
4. Tier-4 Repetition & Frequency Habit Detection (synthesizes recurring habits after 3+ occurrences).

Subscribed to TurnCompleteEvent off the hot path (zero latency added to user responses).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.infrastructure.repositories.state_repository import StateRepository
from src.shared.logger import setup_logger

logger = setup_logger("ProactiveMemoryService")

KEY_PATTERN_HISTORY = "memory:turn_pattern_history"

# Fast heuristic cues indicating personal preferences, assertions, entities, or corrections
_FIRST_PERSON_PATTERNS = re.compile(
    r"\b(i\s+(?:like|love|prefer|hate|dislike|want|need|started|stopped|quit|switched|use|drink|eat|drive|moved|live|work|bought|code|study|graduated))\b",
    re.IGNORECASE,
)

_RELATIONSHIP_PATTERNS = re.compile(
    r"\b(my\s+(?:wife|husband|fianc[eé]e?|girlfriend|boyfriend|brother|sister|mom|mother|dad|father|dog|cat|pet|car|job|boss|friend|wedding))\b",
    re.IGNORECASE,
)

_DIRECTIVE_PATTERNS = re.compile(
    r"\b(?:always|never|remember\s+that|don't\s+forget|from\s+now\s+on|prefer\s+that|instead\s+of|actually|that's\s+wrong)\b",
    re.IGNORECASE,
)


def is_memory_candidate(user_text: Optional[str]) -> bool:
    """Ultra-fast regex check (<1ms) to decide if a turn is worth memory inspection."""
    if not user_text or len(user_text.strip()) < 8:
        return False

    text = user_text.strip()
    return bool(
        _FIRST_PERSON_PATTERNS.search(text)
        or _RELATIONSHIP_PATTERNS.search(text)
        or _DIRECTIVE_PATTERNS.search(text)
    )


def _extract_topic_signature(text: str) -> str:
    """Normalize text into a compact topic signature for repetition tracking."""
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    stop_words = {
        "what", "where", "when", "how", "why", "the", "this", "that",
        "and", "for", "with", "can", "you", "please", "help", "about",
        "some", "have", "from", "just", "like", "will", "would",
    }
    filtered = [w for w in words if w not in stop_words]
    return " ".join(filtered[:5])


class ProactiveMemoryService:
    """Autonomous inline memory extractor and habit pattern detector."""

    def __init__(self, state_repo: Optional[StateRepository] = None):
        self._state_repo = state_repo or StateRepository()
        self._graph_client = None

    async def _get_graph_client(self):
        if self._graph_client is None:
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            self._graph_client = GraphRAGClient()
            await self._graph_client.initialize()
        return self._graph_client

    def _get_pattern_history(self) -> List[Dict[str, Any]]:
        return self._state_repo.get_value(KEY_PATTERN_HISTORY, []) or []

    def _save_pattern_history(self, history: List[Dict[str, Any]]) -> None:
        self._state_repo.set_value(KEY_PATTERN_HISTORY, history)

    def track_repetition(self, user_text: str) -> Optional[str]:
        """Track recurring topics/patterns over a rolling 48-hour window.
        Returns a habit description if a pattern recurs 3 or more times.
        """
        signature = _extract_topic_signature(user_text)
        if not signature or len(signature.split()) < 2:
            return None

        now = time.time()
        window_seconds = 48 * 3600.0
        history = [
            item for item in self._get_pattern_history()
            if now - item.get("ts", 0.0) < window_seconds
        ]

        history.append({"sig": signature, "ts": now})
        self._save_pattern_history(history)

        # Count occurrences of this signature in the window
        occurrences = sum(1 for it in history if it.get("sig") == signature)
        if occurrences == 3:
            logger.info(f"[ HABIT ] Detected recurring topic (3x in 48h): '{signature}'")
            return f"Frequently discusses or asks about '{signature}'."

        return None

    async def distill_facts(self, user_text: str, assistant_text: str) -> List[str]:
        """Tier-2: Use local judge model to distill atomic declarative personal facts."""
        from evals.judges import build_judge_adapter, _extract_json_object

        adapter = build_judge_adapter("local")
        prompt = (
            "You are a personal knowledge extraction engine. "
            "Analyze the conversation exchange between the user and assistant. "
            "Extract any durable personal facts about the user revealed in the exchange.\n\n"
            "CRITICAL RULES:\n"
            "1. Extract ONLY facts that will still be true and relevant months from now "
            "(preferences, identity, habits, life events, recurring patterns, relationships).\n"
            "2. Write each fact as ONE standalone, self-contained declarative sentence in the third person "
            "(e.g. 'The user prefers oat milk.' or 'The user works as a teacher.').\n"
            "3. Do NOT extract temporary tasks ('remind me to...'), code questions, or transient chatter.\n"
            "4. If NO durable personal facts are revealed, return EXACTLY {\"facts\": []}.\n\n"
            f"User: {user_text}\n"
            f"Assistant: {assistant_text}\n\n"
            "Respond with ONLY valid JSON: {\"facts\": [\"...\"]}"
        )

        try:
            resp = await adapter.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                thinking_level="low",
                max_tokens=500,
            )
            raw = (resp.text or "").strip()
            data = _extract_json_object(raw)
            if isinstance(data, dict):
                facts = data.get("facts") or []
                if isinstance(facts, list):
                    return [str(f).strip() for f in facts if f and isinstance(f, str)]
            return []
        except Exception as e:
            logger.warning(f"[ PROACTIVE_MEMORY ] Fact distillation failed: {e}")
            return []

    async def process_turn(self, user_text: str, assistant_text: str) -> List[str]:
        """Return quality-checked proposals; persistence requires desktop approval."""
        if not user_text:
            return []

        # Tier 4: Repetition & habit tracking
        habit_fact = self.track_repetition(user_text)

        # Tier 1: Fast heuristic candidate check
        if not is_memory_candidate(user_text) and not habit_fact:
            return []

        extracted_facts: List[str] = []
        if is_memory_candidate(user_text):
            extracted_facts = await self.distill_facts(user_text, assistant_text)

        if habit_fact and habit_fact not in extracted_facts:
            extracted_facts.append(habit_fact)

        if not extracted_facts:
            return []

        # Tier 3 checks quality, not consent. Never write from this background path.
        from src.core.intelligence.memory_write_gate import MemoryWriteGate
        graph_client = await self._get_graph_client()
        gate = MemoryWriteGate.from_config(graph_client)

        proposed_facts = []
        for raw_fact in extracted_facts:
            try:
                decision = await gate.evaluate(raw_fact)
                for accepted_fact in decision.accepted:
                    if accepted_fact not in proposed_facts:
                        proposed_facts.append(accepted_fact)
            except Exception as e:
                logger.warning("[ PROACTIVE_MEMORY ] Proposal quality check failed: %s", type(e).__name__)

        return proposed_facts
