"""Product-name → browser routing copy must stay explicit (Outlook/ChatGPT cluster)."""

from __future__ import annotations

from src.infrastructure.tools.cloud.preamble_generator import MANUAL_PREAMBLES
from src.infrastructure.tools.web_search_routing import (
    BROWSER_AGENT_HANDOFF_DESC,
    BROWSER_AGENT_SUPERVISOR_ROSTER,
)


def test_browser_handoff_has_product_name_gate() -> None:
    text = BROWSER_AGENT_HANDOFF_DESC.lower()
    assert "product-name" in text or "named surface" in text
    assert "outlook" in text
    assert "chatgpt" in text
    assert "exact" in text and "product" in text
    # Must forbid Gmail/web_search substitution for a named non-Gmail app.
    assert "search_gmail" in text
    assert "web_search" in text
    assert "do not substitute" in text or "not substitute" in text


def test_browser_roster_mentions_named_apps() -> None:
    text = BROWSER_AGENT_SUPERVISOR_ROSTER.lower()
    assert "outlook" in text or "named web app" in text
    assert "exact product" in text


def test_search_gmail_preamble_is_gmail_only() -> None:
    preamble = MANUAL_PREAMBLES["search_gmail"].lower()
    assert "gmail" in preamble
    assert "outlook" in preamble  # explicit exclusion
    assert "not for outlook" in preamble
    keywords_line = [ln for ln in preamble.splitlines() if ln.startswith("keywords:")][0]
    assert "outlook" not in keywords_line
    # Bare "email," / " inbox," as keyword magnets should be gone.
    assert ", email," not in keywords_line
    assert ", inbox," not in keywords_line
