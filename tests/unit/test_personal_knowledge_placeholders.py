"""Tests for personal knowledge seed / placeholder filtering."""

from src.core.intelligence.personal_knowledge_parse import is_placeholder_text


def test_placeholder_scaffold_detected():
    assert is_placeholder_text(
        "[Describe your family and close relationships Chad should know about.]"
    )
    assert is_placeholder_text(
        "[Your name] is the user. Fill in your background here — age, education."
    )


def test_real_facts_not_placeholders():
    assert not is_placeholder_text("Has a Father named David.")
    assert not is_placeholder_text("Significant other is Sam (Fiance)")
