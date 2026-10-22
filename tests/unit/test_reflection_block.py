"""Unit tests for the `## Learned Lessons` persona-block management in reflection.py.

These guard the FIRST code path that writes to Chad's live persona — the merge must be
strictly APPEND-only and must never disturb hand-written content or prior lessons.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.reflection import (
    extract_existing_lessons, merge_lessons_into_content,
    render_lesson_line, split_lesson_block, LESSON_BLOCK_HEADER)

BASE = "# User\nThe user prefers concise answers.\n"


def _lesson(text, conf=0.9, scope="user"):
    return {"scope": scope, "lesson": text, "confidence": conf, "status": "provisional"}


def test_creates_block_when_absent_and_is_append_only():
    out = merge_lessons_into_content(BASE, [_lesson("The user is engaged to their partner")])
    assert LESSON_BLOCK_HEADER in out
    assert "engaged to their partner" in out
    assert out.startswith(BASE)  # nothing before the new block is touched


def test_appends_into_existing_block_preserving_order_and_trailing_section():
    existing = (BASE + "\n## Learned Lessons\n"
                "- Old fact one.  <!-- provisional conf=0.80 added=2026-01-01 -->\n\n"
                "## Other\nkeep me\n")
    out = merge_lessons_into_content(existing, [_lesson("The user works at Acme Corp")])
    assert "Old fact one" in out and "works at Acme Corp" in out
    assert "## Other" in out and "keep me" in out
    # old lesson before new; new lesson stays inside the block (before ## Other)
    assert out.index("Old fact one") < out.index("works at Acme Corp") < out.index("## Other")


def test_extract_round_trips():
    out = merge_lessons_into_content(BASE, [_lesson("The user is engaged to their partner")])
    assert any("engaged to their partner" in e for e in extract_existing_lessons(out))


def test_empty_lessons_is_noop():
    assert merge_lessons_into_content(BASE, []) == BASE


def test_split_returns_none_body_when_no_block():
    before, body, after = split_lesson_block(BASE)
    assert body is None and before == BASE and after == ""


def test_render_line_has_machine_trailer():
    line = render_lesson_line(_lesson("The user is engaged to their partner", conf=0.95), added="2026-06-17")
    assert line.startswith("- ")
    assert "provisional conf=0.95 added=2026-06-17" in line


def test_extract_ignores_block_note_comment():
    """The auto-author note line in the block must not be mistaken for a lesson."""
    out = merge_lessons_into_content(BASE, [_lesson("The user is engaged to their partner")])
    lessons = extract_existing_lessons(out)
    assert all("Auto-authored" not in l for l in lessons)
    assert len(lessons) == 1
