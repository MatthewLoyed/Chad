"""Unit tests for web content extraction quality gates and cascade."""

from __future__ import annotations

from src.tools.web.content_extraction import (
    extract_from_html,
    llm_friendliness_score,
    passes_quality_gate,
    select_page_or_snippet,
)

CSS_SOUP = """
<html><head><title>Cordova Media | Home</title></head><body>
Cordova Media | Home html.m {background-color:rgba(229,225,221,1);}
.d .st-primary {border-style:solid;padding:14px;background-color:rgba(203,184,165,1);}
.d .st-primary span {color:rgba(255,255,255,1);font-family:'studiio thin';font-size:18px;}
</body></html>
"""

GOOD_ARTICLE = """
<html><head><title>Packages</title></head><body><main>
<h1>Wedding Videography Packages</h1>
<p>Our eight-hour wedding package includes two videographers, drone coverage,
and a highlight film. Packages start at $3,600 plus tax. Travel fees may apply
for destinations outside North Alabama. Contact us for a custom quote.</p>
<p>We specialize in documentary-style storytelling for couples across Alabama.</p>
</main></body></html>
"""

ACCESS_DENIED = """
<html><body>Access Denied. You don't have permission to access this server.
Reference #18.7ac84d17. https://errors.edgesuite.net/18.7ac84d17</body></html>
"""

LOADING_SPA = """
<html><body>Loading Loading Loading Loading Loading Loading Loading Loading
Bummer! This vendor is no longer active.</body></html>
"""


def test_quality_gate_rejects_css_soup():
    text = "Cordova Media | Home html.m {background-color:rgba(229,225,221,1);} .d .st-primary {padding:14px;}"
    ok, reason = passes_quality_gate(text)
    assert not ok
    assert reason == "css_noise"


def test_quality_gate_rejects_access_denied():
    ok, reason = passes_quality_gate("Access Denied You don't have permission to access this server.")
    assert not ok
    assert reason and reason.startswith("blocklist:")


def test_quality_gate_accepts_prose():
    text = (
        "North Alabama Wedding and Production Videographer traveling to capture "
        "your special moments with cinematic documentary style."
    )
    ok, _ = passes_quality_gate(text)
    assert ok


def test_extract_from_html_article():
    result = extract_from_html(GOOD_ARTICLE)
    assert result.ok
    assert "Wedding Videography" in result.content
    assert "3600" in result.content or "3,600" in result.content
    assert result.method.startswith("trafilatura")


def test_extract_from_html_css_shell_not_ok():
    result = extract_from_html(CSS_SOUP)
    assert not result.ok
    assert result.reject_reason in {"css_noise", "low_prose", "too short", "quality gate failed", "no extractor produced text"}


def test_llm_friendliness_score_zero_for_garbage():
    assert llm_friendliness_score("html.m {background-color:rgba(1,1,1,1);}") == 0.0


def test_select_page_or_snippet_prefers_richer_snippet():
    thin_page = extract_from_html(
        "<html><body><nav>Weddings branding Real Estate Media CORDOVA MEDIA</nav></body></html>"
    )
    snippet = (
        "North Alabama Wedding and Production Videographer traveling to capture "
        "your special moments with cinematic documentary style."
    )
    content, source, ok, note = select_page_or_snippet(thin_page, snippet)
    assert ok
    assert source == "search_snippet"
    assert "North Alabama" in content
    assert note and "richer" in note


def test_llm_friendliness_score_positive_for_prose():
    text = (
        "North Alabama wedding videographer offering cinematic packages with drone "
        "coverage and highlight films for couples across the region."
    )
    assert llm_friendliness_score(text) > 15
