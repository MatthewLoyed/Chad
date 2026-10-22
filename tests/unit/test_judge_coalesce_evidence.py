"""Unit: judge evidence coalesce collapses YAML dumps without dropping subjects."""
from evals.judges import coalesce_tool_results_for_judge


def test_coalesce_collapses_yaml_fence_and_caps():
    yaml_block = "```yaml\n" + ("- link: /foo\n" * 500) + "```"
    text = (
        "browser_snapshot -> ok\n"
        f"{yaml_block}\n"
        "search_gmail -> Found 1 emails:\n"
        "1. subject='Hello' from=Sam\n"
    )
    out = coalesce_tool_results_for_judge(text, max_chars=2000)
    assert "yaml snapshot collapsed" in out
    assert "Hello" in out
    assert "```yaml" not in out
    assert len(out) <= 2100  # allow truncate footer


def test_coalesce_soft_caps_huge_plain_text():
    huge = "x" * 50000
    out = coalesce_tool_results_for_judge(huge, max_chars=1000)
    assert len(out) < 1500
    assert "collapsed" in out or "truncated for judge context" in out
