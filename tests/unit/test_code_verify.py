"""Unit tests for deterministic code verification gates."""

import pytest

from src.tools.coding.code_verify import verify_source, verify_source_or_html


def test_python_valid():
    r = verify_source("script.py", "def hello():\n    return 1\n")
    assert r.ok
    assert r.gate == "python_syntax"


def test_python_syntax_error():
    r = verify_source("script.py", "def hello(\n")
    assert not r.ok
    assert r.gate == "python_syntax"
    assert "line" in (r.error or "").lower()


def test_json_valid():
    r = verify_source("data.json", '{"a": 1}')
    assert r.ok


def test_json_invalid():
    r = verify_source("data.json", '{"a":')
    assert not r.ok


def test_html_balanced():
    html = "<!DOCTYPE html><html><head><title>T</title></head><body><p>Hi</p></body></html>"
    r = verify_source("page.html", html)
    assert r.ok


def test_html_unclosed_tag():
    html = "<html><body><div><p>Hi</body></html>"
    r = verify_source("page.html", html)
    assert not r.ok
    assert r.gate == "html_syntax"


def test_css_balanced():
    r = verify_source("style.css", "body { color: red; }")
    assert r.ok


def test_css_unbalanced():
    r = verify_source("style.css", "body { color: red; ")
    assert not r.ok


def test_skip_unknown_extension():
    r = verify_source("readme.md", "# Hello")
    assert r.ok
    assert r.gate == "skip"


def test_verify_source_or_html_strips_fences():
    ok, err = verify_source_or_html(
        "app.html",
        "```html\n<html><body><p>OK</p></body></html>\n```",
    )
    assert ok
    assert err == ""


def test_verify_source_or_html_rejects_bad():
    ok, err = verify_source_or_html("app.html", "<div><span></div>")
    assert not ok
    assert "CODE_VERIFY_FAILED" in err
    assert "NOT saved" in err
