"""Unit tests for src/tools/filesystem/pdf_reader.py's extract_pdf_text core.

Generates real text-bearing PDFs with reportlab (a Brain dependency) as fixtures, so this
exercises the actual pypdf extraction path rather than a mock."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.filesystem.pdf_reader import extract_pdf_text


def _make_pdf(path: Path, pages_text: list[str]) -> Path:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    c = canvas.Canvas(str(path), pagesize=letter)
    for text in pages_text:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    return path


def test_extracts_text_from_a_real_pdf(tmp_path):
    pdf = _make_pdf(tmp_path / "report.pdf", ["Advisor initials: JRL", "Second page content"])
    result = extract_pdf_text(pdf)
    assert result["ok"] is True
    assert "Advisor initials: JRL" in result["text"]
    assert result["n_pages"] == 2
    assert result["pages_read"] == 2
    assert result["truncated"] is False
    assert result["image_only"] is False


def test_missing_file_returns_error():
    result = extract_pdf_text("C:/nope/does_not_exist.pdf")
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_non_pdf_suffix_rejected(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    result = extract_pdf_text(txt)
    assert result["ok"] is False
    assert "not a PDF" in result["error"]


def test_max_pages_truncates(tmp_path):
    pdf = _make_pdf(tmp_path / "long.pdf", [f"Page {i}" for i in range(10)])
    result = extract_pdf_text(pdf, max_pages=3)
    assert result["ok"] is True
    assert result["pages_read"] == 3
    assert result["n_pages"] == 10
    assert result["truncated"] is True


def test_max_chars_truncates(tmp_path):
    pdf = _make_pdf(tmp_path / "wide.pdf", ["X" * 500 for _ in range(5)])
    result = extract_pdf_text(pdf, max_chars=600)
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["text"]) <= 700  # capped near the budget (plus a page's join newline)


def test_image_only_pdf_flagged(tmp_path):
    # A PDF with no drawn text -> parses fine but yields nothing extractable (a scan surrogate).
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    p = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.showPage()
    c.save()
    result = extract_pdf_text(p)
    assert result["ok"] is True
    assert result["image_only"] is True
    assert result["text"] == ""
