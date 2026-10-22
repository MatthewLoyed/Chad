"""Unit tests for browser page-surface classification (list vs conversation detail)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.page_surface import (
    PageSurface,
    classify_page_surface,
    extract_page_url,
    format_surface_banner,
    looks_like_truncated_preview,
)
from evals.core import check_live_assertions


class _FakeTrace:
    def __init__(self, tool_results="", tool_names=None, duration_s=1.0, final_response="hi"):
        self.tool_results = tool_results
        self.tool_names = tool_names or ["browser_snapshot"]
        self.duration_s = duration_s
        self.final_response = final_response
        self.plan = None
        self.tool_calls = []
        self.stop_reason = "task_completed"
        self.total_tokens = 10


class TestClassifyPageSurface:
    def test_theknot_inbox_list(self):
        assert classify_page_surface("https://www.theknot.com/dashboard/inbox") == PageSurface.INBOX_LIST
        assert classify_page_surface("https://www.theknot.com/inbox/conversations") == PageSurface.INBOX_LIST

    def test_theknot_conversation_detail(self):
        url = "https://www.theknot.com/inbox/conversations/56cc027d-6e26-49a0-af76-484f8277aec6"
        assert classify_page_surface(url) == PageSurface.CONVERSATION_DETAIL

    def test_gmail_inbox_vs_message(self):
        assert classify_page_surface("https://mail.google.com/mail/u/0/#inbox") == PageSurface.INBOX_LIST
        assert (
            classify_page_surface("https://mail.google.com/mail/u/0/#inbox/FMfcgzQabc123XYZ")
            == PageSurface.CONVERSATION_DETAIL
        )

    def test_search_results(self):
        assert classify_page_surface("https://www.amazon.com/s?k=poster") == PageSurface.SEARCH_RESULTS

    def test_login(self):
        assert classify_page_surface("https://www.theknot.com/login") == PageSurface.LOGIN

    def test_unknown(self):
        assert classify_page_surface("https://example.com/about") == PageSurface.UNKNOWN
        assert classify_page_surface(None) == PageSurface.UNKNOWN

    def test_parked_domain(self):
        assert classify_page_surface("https://forsale.godaddy.com/notebooklm.app") == PageSurface.PARKED_DOMAIN
        assert classify_page_surface("https://dan.com/buy-domain/notebooklm.com") == PageSurface.PARKED_DOMAIN
        assert classify_page_surface("https://www.godaddy.com/domainsearch/find?checkAvail=1") == PageSurface.PARKED_DOMAIN
        assert classify_page_surface("https://notebooklm.app", text="Buy this domain now!\nDomain is for sale.") == PageSurface.PARKED_DOMAIN



class TestExtractAndBanner:
    def test_extract_page_url(self):
        dump = "### Page\n- Page URL: https://www.theknot.com/dashboard/inbox\n- Page Title: Inbox"
        assert extract_page_url(dump) == "https://www.theknot.com/dashboard/inbox"

    def test_list_banner_includes_gate(self):
        banner = format_surface_banner(PageSurface.INBOX_LIST, "https://x/inbox")
        assert "PAGE SURFACE: inbox_list" in banner
        assert "SURFACE GATE" in banner
        assert "PREVIEW" in banner

    def test_detail_banner(self):
        banner = format_surface_banner(
            PageSurface.CONVERSATION_DETAIL,
            "https://x/inbox/conversations/1",
        )
        assert "conversation_detail" in banner
        assert "SURFACE GATE" not in banner

    def test_truncated_preview_heuristic(self):
        assert looks_like_truncated_preview("Looking forward to working with you so you'll")
        assert looks_like_truncated_preview("Ready when you are...")
        assert not looks_like_truncated_preview("The deposit is $100 due upon signing.")


class TestLiveExpectToolResultsRegex:
    def test_passes_when_conversation_opened(self):
        trace = _FakeTrace(
            tool_results="[PAGE SURFACE: conversation_detail] url=https://x/inbox/conversations/abc"
        )
        fails = check_live_assertions(
            {"tool_results_regex_any": ["/inbox/conversations?/[^/\\s\"']+", "PAGE SURFACE: conversation_detail"]},
            trace,
        )
        assert fails == []

    def test_fails_on_list_only(self):
        trace = _FakeTrace(tool_results="[PAGE SURFACE: inbox_list] url=https://x/inbox\nFound N emails")
        fails = check_live_assertions(
            {"tool_results_regex_any": ["/inbox/conversations?/[^/\\s\"']+", "PAGE SURFACE: conversation_detail"]},
            trace,
        )
        assert any("tool_results_regex_any" in f for f in fails)
