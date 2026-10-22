"""Tests for artifact auto-preview payload extraction."""

from src.tools.coding.artifact_display import artifact_display_payload


def test_html_build_auto_preview_payload():
    result = {
        "success": True,
        "download_token": "tok-abc",
        "filename": "site.html",
        "auto_preview": True,
    }
    payload = artifact_display_payload("build_html_file", result)
    assert payload == {
        "auto_preview": True,
        "download_token": "tok-abc",
        "filename": "site.html",
    }


def test_no_preview_without_flag():
    result = {
        "success": True,
        "download_token": "tok-abc",
        "filename": "notes.txt",
    }
    assert artifact_display_payload("write_file", result) == {}
