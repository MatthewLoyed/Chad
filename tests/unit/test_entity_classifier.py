import pytest
from src.core.intelligence.entity_classifier import EntityClassifier


class TestEntityClassifier:
    """Test suite for lightweight, zero-latency entity classifier."""

    # Positive Cases: Should trigger entity grounding
    @pytest.mark.parametrize(
        "query,expected_entity,is_domain",
        [
            (
                "go to my gemini notebook .com for wireless networks and add any new lectures from canvas that arent in there already",
                "gemini notebook .com",
                True,
            ),
            (
                "go to my gemini notebook and check lecture slides",
                "gemini notebook",
                False,
            ),
            (
                "navigate to perplexity and search for latest AI research",
                "perplexity",
                False,
            ),
            (
                "check the supabase dashboard for my quota usage",
                "supabase",
                False,
            ),
            (
                "open chatgpt and paste this prompt",
                "chatgpt",
                False,
            ),
            (
                "log into notion.so and find the product specs",
                "notion.so",
                True,
            ),
            (
                "visit https://linear.app/projects to view the sprint",
                "https://linear.app/projects",
                True,
            ),
        ],
    )
    def test_positive_classifications(self, query: str, expected_entity: str, is_domain: bool):
        result = EntityClassifier.classify(query)
        assert result.should_ground is True, f"Failed for query: {query}"
        assert expected_entity.lower() in result.entity.lower()
        assert result.is_domain == is_domain

    # Negative Cases: Should NOT trigger entity grounding
    @pytest.mark.parametrize(
        "query,reason",
        [
            ("organize my downloads folder by file type", "local_filesystem"),
            ("move all pdf files from documents to desktop", "local_filesystem"),
            ("what assignments are due on canvas next week?", "local_tool"),
            ("turn off the bedroom lamp", "smart_home"),
            ("what's on my calendar for tomorrow afternoon?", "calendar"),
            ("write a python script to parse a json file", "pure_code"),
            ("implement a binary search tree in python", "pure_code"),
            ("hi there, how are you doing today?", "conversational"),
            ("who are you and what can you do?", "conversational"),
            ("go to sleep", "stop_word"),
            ("open the file report.pdf", "local_file"),
        ],
    )
    def test_negative_classifications(self, query: str, reason: str):
        result = EntityClassifier.classify(query)
        assert result.should_ground is False, f"Unexpected grounding for: {query} (reason: {result.reason})"
