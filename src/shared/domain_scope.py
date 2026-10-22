"""
Domain scoping — the single definition of what a domain agent is allowed to see.

Domain agents (Financial, Fitness, School, ...) exist to keep unrelated personal data out
of each other's context. That guarantee is only as good as its weakest read path, so the
rule lives here once and every retrieval site calls it rather than re-implementing the
comparison. If you are adding a new retrieval path, call `matches_domain` — do not inline
`record.get("domain") == active`, which silently drops the untagged/global tier.

**The layered rule:** a turn scoped to domain D sees
  - records tagged D, and
  - untagged records (the global tier: who the user is, general facts)
and never records tagged with any other domain.

An unscoped turn (D is None) sees only untagged records — a general chat must not surface
financial or medical facts just because it did not pick a domain.
"""

from typing import Any, Mapping, Optional


def normalize_domain(domain: Optional[str]) -> Optional[str]:
    """Collapse empty strings/whitespace to None so '' can't be treated as a real domain."""
    if domain is None:
        return None
    cleaned = str(domain).strip()
    return cleaned or None


def matches_domain(record_domain: Optional[str], active_domain: Optional[str]) -> bool:
    """Return True if a record tagged `record_domain` is visible to `active_domain`."""
    record_domain = normalize_domain(record_domain)
    if record_domain is None:
        return True  # untagged == global tier, visible everywhere
    return record_domain == normalize_domain(active_domain)


def record_matches_domain(record: Mapping[str, Any], active_domain: Optional[str],
                          key: str = "domain") -> bool:
    """`matches_domain` for a dict-like row/node whose scope lives under `key`."""
    return matches_domain(record.get(key) if record else None, active_domain)


def metadata_matches_domain(metadata: Optional[Any],
                            active_domain: Optional[str]) -> bool:
    """`matches_domain` for a sqlite memory row whose scope lives in its metadata JSON."""
    if isinstance(metadata, str):
        try:
            import json
            metadata = json.loads(metadata) if metadata else {}
        except Exception:
            metadata = {}
    domain_val = metadata.get("domain") if isinstance(metadata, Mapping) else None
    return matches_domain(domain_val, active_domain)
