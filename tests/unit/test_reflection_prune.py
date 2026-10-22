"""Unit tests for the lesson GC / prune pass (#640).

Covers: parsing lesson entries with their machine trailer, flagging outage-shaped
lessons already written to the persona block, expiring old un-promoted provisional
lessons, and collapsing in-block near-duplicates (keeping the higher-confidence one).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.reflection import (
    LESSON_BLOCK_HEADER,
    analyze_lessons_for_prune,
    apply_prune,
    parse_lesson_entries,
    render_prune_report,
)

BASE = "# Tools\nSome hand-written doctrine.\n"


def _block(*lines: str) -> str:
    return BASE + f"\n{LESSON_BLOCK_HEADER}\n" + "\n".join(lines) + "\n"


# ── parse_lesson_entries ──────────────────────────────────────────────────────


def test_parse_entries_reads_trailer_metadata():
    content = _block("- The lamp is named bedroom.  <!-- provisional conf=0.90 added=2026-06-17 -->")
    entries = parse_lesson_entries(content)
    assert len(entries) == 1
    e = entries[0]
    assert e.text == "The lamp is named bedroom."
    assert e.status == "provisional"
    assert e.confidence == 0.90
    assert e.added == "2026-06-17"


def test_parse_entries_marks_hand_written_lines_unmanaged():
    content = _block("- A hand-written lesson with no trailer.")
    entries = parse_lesson_entries(content)
    assert len(entries) == 1
    assert entries[0].status == "unmanaged"
    assert entries[0].added is None


def test_parse_entries_empty_when_no_block():
    assert parse_lesson_entries(BASE) == []


# ── outage detection in the GC pass ───────────────────────────────────────────


def test_prune_flags_outage_shaped_lesson():
    """The exact #640 regression: this line was hand-pruned in PR #638; the GC pass
    must catch it automatically going forward."""
    content = _block(
        "- Sandboxed execution tools (run_sandboxed_bash, run_sandboxed_python) are "
        "currently returning 401 Unauthorized errors due to malformed authorization "
        "headers.  <!-- provisional conf=0.90 added=2026-07-10 -->"
    )
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    assert len(report.outage) == 1
    assert not report.expired
    assert not report.duplicates
    assert report.kept_count == 0


def test_prune_never_flags_unmanaged_lines():
    """Hand-authored lines (no trailer) are never auto-pruned, even if they happen to
    use outage-ish language — a human wrote it on purpose."""
    content = _block("- The API is currently down and that's expected, ignore it.")
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    assert not report.outage
    assert report.kept_count == 1


# ── TTL expiry ─────────────────────────────────────────────────────────────────


def test_prune_expires_old_provisional_lesson():
    content = _block(
        "- An old lesson that was never promoted.  <!-- provisional conf=0.80 added=2026-05-01 -->"
    )
    report = analyze_lessons_for_prune(content, ttl_days=30, today="2026-07-11")
    assert len(report.expired) == 1
    assert report.expired[0]["age_days"] > 30


def test_prune_keeps_recent_provisional_lesson():
    content = _block(
        "- A fresh lesson.  <!-- provisional conf=0.80 added=2026-07-05 -->"
    )
    report = analyze_lessons_for_prune(content, ttl_days=30, today="2026-07-11")
    assert not report.expired
    assert report.kept_count == 1


def test_prune_never_expires_active_lessons():
    """`active` status means graduated/reviewed — TTL only applies to provisional."""
    content = _block(
        "- A graduated lesson.  <!-- active conf=0.95 added=2026-01-01 -->"
    )
    report = analyze_lessons_for_prune(content, ttl_days=30, today="2026-07-11")
    assert not report.expired
    assert report.kept_count == 1


# ── in-block duplicate collapsing ─────────────────────────────────────────────


def test_prune_flags_near_duplicate_keeping_higher_confidence():
    """The #640 lamp-fact regression: same fact admitted twice at different confidences."""
    content = _block(
        "- The user's lamp is named bedroom.  <!-- provisional conf=0.90 added=2026-06-17 -->",
        "- The user's lamp is named bedroom in the app.  <!-- provisional conf=0.95 added=2026-07-04 -->",
    )
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    assert len(report.duplicates) == 1
    assert report.duplicates[0]["added"] == "2026-06-17"  # lower-confidence one dropped
    assert report.kept_count == 1


def test_prune_distinct_lessons_are_not_flagged_as_duplicates():
    content = _block(
        "- The user prefers dark mode everywhere.  <!-- provisional conf=0.9 added=2026-07-01 -->",
        "- The user works late evenings, not mornings.  <!-- provisional conf=0.9 added=2026-07-01 -->",
    )
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    assert not report.duplicates
    assert report.kept_count == 2


# ── apply_prune / render_prune_report ─────────────────────────────────────────


def test_apply_prune_removes_flagged_lines_only():
    content = _block(
        "- Keep me.  <!-- active conf=0.95 added=2026-01-01 -->",
        "- Currently down, remove me.  <!-- provisional conf=0.9 added=2026-07-10 -->",
    )
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    pruned = apply_prune(content, report)
    assert "Keep me" in pruned
    assert "Currently down" not in pruned


def test_apply_prune_noop_when_nothing_flagged():
    content = _block("- A perfectly fine, fresh lesson.  <!-- provisional conf=0.9 added=2026-07-10 -->")
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    assert apply_prune(content, report) == content


def test_render_prune_report_lists_all_categories():
    content = _block(
        "- Currently returning 500 errors.  <!-- provisional conf=0.9 added=2026-07-10 -->",
        "- An old stale lesson.  <!-- provisional conf=0.8 added=2026-05-01 -->",
    )
    report = analyze_lessons_for_prune(content, ttl_days=30, today="2026-07-11")
    rendered = render_prune_report(report)
    assert "Outage-shaped" in rendered
    assert "Expired" in rendered
    assert "to remove: 2" in rendered


def test_render_prune_report_nothing_to_prune():
    content = _block("- A fine lesson.  <!-- active conf=0.9 added=2026-01-01 -->")
    report = analyze_lessons_for_prune(content, today="2026-07-11")
    assert "Nothing to prune" in render_prune_report(report)
