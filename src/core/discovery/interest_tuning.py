"""
Interest Tuning Engine - Adaptive User Taste Profile & Feedback Calibration.

Learns user preferences dynamically through conversational feedback, reaction cues,
and explicit tuning commands. Applies continuous scoring to discovered content.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.core.discovery.models import (
    ContentCategory,
    DiscoveryItem,
    InterestProfile,
)
from src.infrastructure.repositories.state_repository import StateRepository
from src.shared.logger import setup_logger

logger = setup_logger("InterestTuningEngine")

PROFILE_KEY = "discovery:interest_profile"
LEGACY_AFFINITY_KEY = "curiosity:affinity"

POSITIVE_CUES = (
    "lmao", "😂", "🔥", "fire", "sick", "wild", "lol", "haha",
    "goat", "w", "based", "love it", "more like this", "cool", "awesome",
    "banger", "god tier", "peak", "clutch", "yes please", "bookmark"
)

NEGATIVE_CUES = (
    "mid", "cringe", "meh", "stop", "trash", "boring", "l", "nah",
    "hate it", "less of this", "terrible", "unsub", "don't care", "skip"
)


class InterestTuningEngine:
    """Manages multi-dimensional user interest profiles and dynamic feedback tuning."""

    def __init__(self, state_repo: Optional[StateRepository] = None):
        self._state_repo = state_repo or StateRepository()

    def get_profile(self) -> InterestProfile:
        """Fetch current interest profile, migrating legacy affinity if needed."""
        raw = self._state_repo.get_value(PROFILE_KEY, None)
        if raw and isinstance(raw, dict):
            return InterestProfile.from_dict(raw)

        # Fallback / migration from legacy curiosity:affinity
        legacy = self._state_repo.get_value(LEGACY_AFFINITY_KEY, {}) or {}
        profile = InterestProfile()
        if legacy and isinstance(legacy, dict):
            profile.categories.update({k: float(v) for k, v in legacy.items()})
        self.save_profile(profile)
        return profile

    def save_profile(self, profile: InterestProfile) -> None:
        """Persist profile and keep legacy curiosity:affinity in sync."""
        profile.last_updated_at = time.time()
        self._state_repo.set_value(PROFILE_KEY, profile.to_dict())
        # Keep legacy curiosity:affinity key synced for backward compatibility
        self._state_repo.set_value(LEGACY_AFFINITY_KEY, profile.categories)

    def tune_category(self, category: str, weight: float) -> float:
        """Explicitly set a category affinity weight (clamped 0.1 to 3.0)."""
        cat = category.strip().lower()
        clamped = max(0.1, min(3.0, round(float(weight), 2)))
        profile = self.get_profile()
        profile.categories[cat] = clamped
        self.save_profile(profile)
        logger.info(f"[ INTEREST ] Tuned category '{cat}' to {clamped}")
        return clamped

    def tune_topic(self, topic: str, weight: float) -> float:
        """Explicitly set a fine-grained topic weight (clamped 0.1 to 3.0)."""
        top = topic.strip().lower()
        clamped = max(0.1, min(3.0, round(float(weight), 2)))
        profile = self.get_profile()
        profile.topics[top] = clamped
        self.save_profile(profile)
        logger.info(f"[ INTEREST ] Tuned topic '{top}' to {clamped}")
        return clamped

    def block_domain(self, domain: str) -> bool:
        """Block an entire domain from discovery results."""
        clean = domain.strip().lower().replace("http://", "").replace("https://", "").split("/")[0]
        if not clean:
            return False
        profile = self.get_profile()
        if clean not in profile.blocked_domains:
            profile.blocked_domains.append(clean)
            self.save_profile(profile)
            logger.info(f"[ INTEREST ] Blocked domain: {clean}")
            return True
        return False

    def unblock_domain(self, domain: str) -> bool:
        """Unblock a previously blocked domain."""
        clean = domain.strip().lower().replace("http://", "").replace("https://", "").split("/")[0]
        profile = self.get_profile()
        if clean in profile.blocked_domains:
            profile.blocked_domains.remove(clean)
            self.save_profile(profile)
            logger.info(f"[ INTEREST ] Unblocked domain: {clean}")
            return True
        return False

    def is_domain_blocked(self, url: str) -> bool:
        """Check if URL's hostname matches any blocked domains."""
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            profile = self.get_profile()
            return any(blocked in host for blocked in profile.blocked_domains)
        except Exception:
            return False

    def record_feedback(
        self,
        feedback_text: str,
        category: Optional[str] = None,
        topic: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adjust profile weights based on conversational reactions or explicit votes.

        Returns:
            Dict summary of applied adjustments.
        """
        text = (feedback_text or "").lower().strip()
        words = set(re.findall(r"\b\w+\b", text))

        def _has_cue(cues_tuple: tuple) -> bool:
            for c in cues_tuple:
                if len(c) <= 2 and c.isalnum():
                    if c in words:
                        return True
                elif c in text:
                    return True
            return False

        is_positive = _has_cue(POSITIVE_CUES)
        is_negative = _has_cue(NEGATIVE_CUES)

        delta = 0.0
        if is_positive and not is_negative:
            delta = 0.2
        elif is_negative and not is_positive:
            delta = -0.2

        if delta == 0.0:
            return {"applied": False, "reason": "No sentiment cues detected in feedback"}

        profile = self.get_profile()
        changes = {}

        # 1. Category adjustment
        target_cat = (category or ContentCategory.GENERAL.value).strip().lower()
        old_cat_val = profile.categories.get(target_cat, 1.0)
        new_cat_val = max(0.1, min(3.0, round(old_cat_val + delta, 2)))
        profile.categories[target_cat] = new_cat_val
        changes["category"] = {target_cat: new_cat_val}

        # 2. Topic adjustment (if provided or extracted)
        target_topic = (topic or "").strip().lower()
        if target_topic:
            old_top_val = profile.topics.get(target_topic, 1.0)
            topic_delta = delta * 1.25  # Topics adjust slightly faster than broad categories
            new_top_val = max(0.1, min(3.0, round(old_top_val + topic_delta, 2)))
            profile.topics[target_topic] = new_top_val
            changes["topic"] = {target_topic: new_top_val}

        # 3. Log feedback history
        feedback_entry = {
            "timestamp": time.time(),
            "text": feedback_text[:100],
            "delta": delta,
            "category": target_cat,
            "topic": target_topic or None,
            "url": url,
        }
        profile.feedback_history.append(feedback_entry)
        if len(profile.feedback_history) > 100:
            profile.feedback_history = profile.feedback_history[-100:]

        self.save_profile(profile)
        logger.info(f"[ INTEREST ] Feedback processed: delta={delta:+0.2f}, changes={changes}")
        return {
            "applied": True,
            "sentiment": "positive" if delta > 0 else "negative",
            "delta": delta,
            "changes": changes,
        }

    def score_item(self, item: DiscoveryItem) -> float:
        """
        Calculate final affinity score for a candidate discovery item.

        Combines:
        1. Base category weight
        2. Fine-grained topic keyword match bonuses
        3. Blocked domain penalty (0.0 score)
        """
        if self.is_domain_blocked(item.url):
            item.affinity_score = 0.0
            return 0.0

        profile = self.get_profile()
        cat_weight = profile.categories.get(item.category, 1.0)

        # Search searchable text (title + summary + metadata tags)
        search_blob = f"{item.title} {item.summary}".lower()
        if item.metadata and isinstance(item.metadata, dict):
            tags = item.metadata.get("tags") or []
            if isinstance(tags, list):
                search_blob += " " + " ".join(str(t).lower() for t in tags)

        # Topic matching multiplier
        topic_multiplier = 1.0
        matches = 0
        matched_topics = []
        for topic_name, topic_weight in profile.topics.items():
            clean_name = topic_name.replace("_", " ")
            pattern = rf"\b{re.escape(clean_name)}\b"
            if re.search(pattern, search_blob, re.IGNORECASE):
                topic_multiplier *= topic_weight
                matched_topics.append(clean_name)
                matches += 1
                if matches >= 3:
                    break

        # Blend category and topic scores, capped reasonably
        final_affinity = max(0.1, min(5.0, round(cat_weight * topic_multiplier, 3)))
        item.affinity_score = final_affinity
        item.metadata["selection_reason"] = (
            "Matches topics in your interest profile: " + ", ".join(matched_topics) + "."
            if matched_topics else
            f"Selected for the {item.category.replace('_', ' ')} category in your interest profile."
        )
        return final_affinity

    def apply_time_decay(self, elapsed_days: float = 7.0) -> None:
        """
        Gently decay extreme weights toward 1.0 (neutral) to allow tastes to evolve.
        """
        profile = self.get_profile()
        factor = min(0.5, profile.decay_rate * (elapsed_days / 7.0))

        # Decay categories
        for cat, weight in profile.categories.items():
            if weight != 1.0:
                profile.categories[cat] = round(weight + (1.0 - weight) * factor, 3)

        # Decay topics
        for top, weight in profile.topics.items():
            if weight != 1.0:
                profile.topics[top] = round(weight + (1.0 - weight) * factor, 3)

        self.save_profile(profile)
        logger.info(f"[ INTEREST ] Applied decay across profile (elapsed_days={elapsed_days})")

    def reset_to_defaults(self) -> InterestProfile:
        """Reset profile to system default weights."""
        default_profile = InterestProfile()
        self.save_profile(default_profile)
        logger.info("[ INTEREST ] Reset profile to defaults")
        return default_profile
