import pytest

from src.core.intelligence.knowledge_context_format import (
    compact_knowledge_item,
    extract_picker_filename,
    format_knowledge_bullets,
)

PICKER_URL = (
    "https://drive.google.com/picker/minpick/main?parent=https%3A%2F%2Fdrive.google.com"
    "&mreq=%5B%22mireq%22%2C%5B1%2C65%2C%22drive-fe-organize%22%2C%5B%22https%3A%2F%2F"
    "drive.google.com%22%5D%2C%5B%5B%5B1780105571%2C896000000%5D%5D%5D%2C%5Bnull%2C1%2C1%5D"
    "%2C%5B1%2C1%5D%2C1%5D%2C%5B%5B%5B%221dA0mTfCFQmv2LdsfTY0bfeZyllH6zHYkK5PNXjKCMv8%22"
    "%2C%5B%5B%220AILXb5zJckmaUk9PVA%22%2C%22My%20Drive%22%2Cnull%2C%5B5%5D%2Cnull%2Cnull"
    "%2C%5B1%5D%5D%5D%2Cnull%2C%22ExampleBiz_Action_Items.md%22%2C%22application%2Fvnd."
    "google-apps.document%22%2Cnull%2Cnull%2C%5B%5D%2C%5B1%5D%2Cnull%2C%220AILXb5zJckmaUk9PVA"
    "%22%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B3%2C7%5D%5D%5D%2Cnull%2C1%5D%5D"
)


def test_extract_picker_filename():
    assert extract_picker_filename(PICKER_URL) == "ExampleBiz_Action_Items.md"


def test_compact_drive_entry_strips_link_and_date():
    raw = (
        "Modified Google Drive Google Doc: 'ProjectAlpha'\n"
        "Last Edited: 2026-05-14T20:01:47.450Z\n"
        "Access Link: https://docs.google.com/document/d/abc/edit"
    )
    assert compact_knowledge_item(raw) == "Drive Google Doc: 'ProjectAlpha' (2026-05-14)"


def test_compact_email_shortens_sender_and_snippet():
    raw = (
        "Email from Alex Smith (via Google Drive) <drive-shares@google.com>: "
        "'Folder shared with you: \"ProjectAlpha\"'\n"
        "Snippet: Alex Smith shared a folder Alex Smith has invited you to contribute"
    )
    result = compact_knowledge_item(raw)
    assert result.startswith("Email: 'Folder shared with you: \"ProjectAlpha\"' from Alex Smith")
    assert "drive-shares" not in result
    assert len(result) <= 120


def test_compact_drive_picker_visit_drops_bloated_url():
    raw = f"Visited Web Page: 'My Drive - Google Drive' ({PICKER_URL})"
    assert compact_knowledge_item(raw) == "Drive file: 'ExampleBiz_Action_Items.md'"


def test_compact_filesystem_entry_shortens_date():
    raw = (
        "File: 'wedding_budget.xlsx' at C:\\Users\\example\\Documents\\wedding_budget.xlsx\n"
        "Last Modified: 2026-05-14T20:01:47.450000+00:00"
    )
    assert compact_knowledge_item(raw) == (
        "File: 'wedding_budget.xlsx' at C:\\Users\\example\\Documents\\wedding_budget.xlsx (2026-05-14)"
    )


def test_compact_spam_email_is_dropped():
    raw = (
        "Email from CloudSecurityTeam <spam@aweb.hr>: "
        "'FINAL NOTICE: Your files are scheduled for removal today'\n"
        "Snippet: FINAL NOTICE: Your files are scheduled for removal today"
    )
    assert compact_knowledge_item(raw) is None


def test_format_knowledge_bullets_respects_similarity_and_dedupes():
    items = [
        {"content": "Modified Google Drive Google Doc: 'ProjectAlpha'\nLast Edited: 2026-05-14", "similarity": 0.9},
        {"content": "Modified Google Drive Google Doc: 'ProjectAlpha'\nLast Edited: 2026-05-14", "similarity": 0.8},
        {"content": "Email from nobody: 'hello'\nSnippet: hi", "similarity": 0.1},
    ]
    result = format_knowledge_bullets(items, max_items=5, min_similarity=0.22)
    assert result is not None
    lines = result.splitlines()
    assert len(lines) == 1
    assert lines[0] == "- Drive Google Doc: 'ProjectAlpha' (2026-05-14)"


def test_compact_prose_keeps_query_relevant_sentence():
    """Long family blobs must not prefix-cut 'Fiancée is Sam' to 'Fiancée is S...'."""
    family = (
        "Has a Father named Marcus. Has a Mother named Elena. Has a Brother named Theo. "
        "Has a Sister named Faith. Fiancée is Sam Okafor (wedding April 11, 2028). "
        "Sam's parents are Ruth Okafor and Daniel Okafor."
    )
    result = compact_knowledge_item(
        family,
        max_chars=120,
        query="what is my fiancee's name?",
    )
    assert result is not None
    assert "Sam" in result
    assert "Fiancée is S..." not in result
    assert not result.startswith("Has a Father")
    assert len(result) <= 120


def test_format_knowledge_bullets_caps_count():
    items = [
        {"content": f"Modified Google Drive Google Doc: 'Doc{i}'\nLast Edited: 2026-05-14", "similarity": 0.9}
        for i in range(6)
    ]
    result = format_knowledge_bullets(items, max_items=3, min_similarity=0.0)
    assert len(result.splitlines()) == 3


@pytest.mark.parametrize("similarity", [0, None, float("nan"), float("inf"), .396])
def test_semantic_cutoff_rejects_unknown_or_weak_scores(similarity):
    assert format_knowledge_bullets([{"content": "Unrelated recipient document", "similarity": similarity}],
                                  max_items=5, min_similarity=.42) is None


def test_semantic_cutoff_retains_explicit_document_match():
    assert format_knowledge_bullets([{"content": "Requested resume", "similarity": .443}],
                                  max_items=5, min_similarity=.42) == "- Requested resume"
