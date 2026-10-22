"""Unit tests for Gmail ingest query shaping."""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.intelligence.ingestion.google_workspace import build_gmail_ingest_query


def test_includes_sent_by_default():
    q = build_gmail_ingest_query(lookback_days=90)
    assert "(in:inbox OR in:sent)" in q
    assert "-label:SENT" not in q
    assert "-category:promotions" in q
    assert "-category:social" in q
    assert "-category:forums" in q
    assert "-from:noreply" in q
    assert "after:" in q


def test_can_exclude_sent():
    q = build_gmail_ingest_query(include_sent=False, lookback_days=0)
    assert "-label:SENT" in q
    assert "in:sent" not in q


def test_watermark_uses_after_epoch():
    ts = datetime(2025, 6, 3, 18, 40, tzinfo=timezone.utc)
    q = build_gmail_ingest_query(last_sync=ts, lookback_days=9999)
    assert f"after:{int(ts.timestamp())}" in q


def test_extra_query_appended():
    q = build_gmail_ingest_query(extra_query="-category:updates", lookback_days=0)
    assert "(-category:updates)" in q
