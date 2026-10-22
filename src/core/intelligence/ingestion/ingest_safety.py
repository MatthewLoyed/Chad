"""Guards so a bad ingest/reconcile cannot wipe precious local memory rows."""

from __future__ import annotations

from typing import Iterable, Optional, Set, Tuple


def plan_catalog_reconcile_deletes(
    existing_file_ids: Iterable[str],
    keep_file_ids: Iterable[str],
    *,
    max_delete_ratio: float = 0.25,
    allow_empty_wipe: bool = False,
) -> Tuple[Set[str], Optional[str]]:
    """Decide which catalog file_ids are safe to delete.

    Returns ``(ids_to_delete, skip_reason)``. When ``skip_reason`` is set, callers
    must not delete anything (including orphans).

    Rules:
    - Empty keep set + existing rows → refuse unless ``allow_empty_wipe``.
    - If would-delete / existing > ``max_delete_ratio`` → refuse (partial API
      failure / wrong query must not mass-delete a healthy catalog).
    """
    existing = {fid for fid in existing_file_ids if fid}
    keep = {fid for fid in keep_file_ids if fid}
    if not existing:
        return set(), None

    if not keep:
        if allow_empty_wipe:
            return existing, None
        return set(), (
            f"refusing empty keep set while {len(existing)} catalog row(s) exist"
        )

    to_delete = existing - keep
    if not to_delete:
        return set(), None

    ratio = len(to_delete) / len(existing)
    if max_delete_ratio < 1.0 and ratio > max_delete_ratio:
        return set(), (
            f"would delete {len(to_delete)}/{len(existing)} ({ratio:.0%}) "
            f"exceeds max_reconcile_delete_ratio={max_delete_ratio:.0%}"
        )
    return to_delete, None
