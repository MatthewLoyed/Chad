"""Tests for ingest mass-delete / reconcile safety gates."""

from __future__ import annotations

from src.core.intelligence.ingestion.ingest_safety import plan_catalog_reconcile_deletes


def test_refuse_empty_keep_when_catalog_has_rows():
    to_delete, skip = plan_catalog_reconcile_deletes(
        ["a", "b", "c"],
        [],
        max_delete_ratio=0.25,
        allow_empty_wipe=False,
    )
    assert to_delete == set()
    assert skip is not None
    assert "empty keep" in skip


def test_allow_empty_wipe_when_explicit():
    to_delete, skip = plan_catalog_reconcile_deletes(
        ["a", "b"],
        [],
        allow_empty_wipe=True,
    )
    assert skip is None
    assert to_delete == {"a", "b"}


def test_refuse_high_delete_ratio():
    existing = [f"f{i}" for i in range(100)]
    keep = [f"f{i}" for i in range(50)]  # would delete 50%
    to_delete, skip = plan_catalog_reconcile_deletes(
        existing, keep, max_delete_ratio=0.25
    )
    assert to_delete == set()
    assert skip is not None
    assert "exceeds" in skip


def test_allow_small_reconcile():
    existing = [f"f{i}" for i in range(100)]
    keep = [f"f{i}" for i in range(90)]  # delete 10%
    to_delete, skip = plan_catalog_reconcile_deletes(
        existing, keep, max_delete_ratio=0.25
    )
    assert skip is None
    assert to_delete == {f"f{i}" for i in range(90, 100)}


def test_noop_when_nothing_existing():
    to_delete, skip = plan_catalog_reconcile_deletes([], ["a"], max_delete_ratio=0.25)
    assert to_delete == set()
    assert skip is None
