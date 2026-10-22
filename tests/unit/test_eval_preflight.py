"""Tests for the eval preflight gate.

Preflight's whole value is that it is TRUSTWORTHY: if it says "clear", a long unattended
run should not die on a dependency. So these tests focus on the ways a gate can lie —
inferring the wrong requirements, inventing a credential horizon it cannot know, or
treating a shape-only probe as proof of a working dependency.
"""

from __future__ import annotations

import json
import time

import pytest

from evals.core import Case
from evals.preflight import (FAIL, OK, WARN, CheckResult, PreflightContext, PreflightReport,
                             capability_for_tool, estimate_run_seconds, requirements_for_cases,
                             run_preflight)
from evals.preflight.checks import _refresh_token_first_seen, check_datasets


# ── capability inference ─────────────────────────────────────────────────────


@pytest.mark.parametrize("tool,capability", [
    ("search_gmail", "google_oauth"),
    ("google_calendar_check", "google_oauth"),
    ("google_drive_search", "google_oauth"),
    ("web_search", "web_search"),
    ("run_sandboxed_python", "sandbox"),
    ("compile_typst", "typst"),
    ("hubspace_control", "smart_home"),
    ("send_telegram_message", "telegram"),
    ("get_upcoming_assignments", "canvas"),
    # Dynamic families the top-level ToolRegistry never lists.
    ("delegate_to_browser_agent", "browser"),
    ("delegate_to_deep_research_agent", "web_search"),
    ("browser_navigate", "browser"),
])
def test_tools_map_to_their_external_dependency(tool, capability):
    assert capability_for_tool(tool) == capability


def test_purely_local_tools_need_nothing_external():
    for tool in ("get_datetime", "manage_user_memory", "read_file", "delegate_to_code_agent"):
        assert capability_for_tool(tool) is None


def test_requirements_are_scoped_to_the_selected_cases():
    """A free tool-selection run must not demand a healthy Gmail token — otherwise nobody
    runs preflight and the gate is worthless."""
    local_only = [Case(id="c1", prompt="what time is it",
                       expect={"tools_include": ["get_datetime"]})]
    reqs = requirements_for_cases(local_only, lanes=["dry-run"])
    assert "google_oauth" not in reqs
    assert "web_search" not in reqs
    assert "lmstudio" in reqs, "the dry-run lane still calls the 9B tool selector"


def test_requirements_pick_up_each_cases_dependency():
    cases = [
        Case(id="a", prompt="check mail", expect={"tools_include": ["search_gmail"]}),
        Case(id="b", prompt="research", expect={"tools_any": ["web_search"]}),
    ]
    reqs = requirements_for_cases(cases, lanes=["live"])
    assert {"google_oauth", "web_search", "server", "auth_token"} <= reqs


def test_dry_run_does_not_require_tool_execution_dependencies():
    """The dry-run lane only captures which tools got BOUND; it never calls them. Demanding
    a live search backend to verify the SELECTOR picks web_search would block the free lane
    for no reason — and a gate that over-blocks is one people bypass."""
    cases = [
        Case(id="a", prompt="research nvidia", expect={"tools_include": ["web_search"]}),
        Case(id="b", prompt="check mail", expect={"tools_include": ["search_gmail"]}),
        Case(id="c", prompt="make a pdf", expect={"tools_include": ["compile_typst"]}),
    ]
    dry = requirements_for_cases(cases, lanes=["dry-run"])
    assert not ({"web_search", "google_oauth", "typst"} & dry)
    assert {"lmstudio", "gate", "datasets", "disk"} <= dry

    # The same cases in an executing lane DO need them.
    live = requirements_for_cases(cases, lanes=["live"])
    assert {"web_search", "google_oauth", "typst"} <= live


def test_negative_cases_do_not_require_the_dependency_they_forbid():
    """A case asserting the model must NOT call a Gmail tool needs no Gmail token. Demanding
    one would make negative cases expensive, and negative cases are the ones that stop a
    suite being satisfiable by a model that over-calls tools."""
    cases = [Case(id="neg", prompt="just chat",
                  expect={"tools_exclude": ["search_gmail", "web_search"]})]
    reqs = requirements_for_cases(cases, lanes=["dry-run"])
    assert "google_oauth" not in reqs
    assert "web_search" not in reqs


def test_lane_drives_run_duration_estimate():
    """The estimate feeds the credential-horizon check, so a workflow run must be modelled
    as far longer than a dry run."""
    dry = estimate_run_seconds(100, ["dry-run"])
    live = estimate_run_seconds(100, ["live"])
    workflow = estimate_run_seconds(100, ["workflow"])
    assert dry < live < workflow
    assert estimate_run_seconds(10, ["live"], repeat=3) == 3 * estimate_run_seconds(10, ["live"])


# ── refresh-token age tracking ───────────────────────────────────────────────


def test_first_observation_reports_unknown_age(tmp_path, monkeypatch):
    """Neither file timestamp can date a refresh token (mtime resets on every access-token
    refresh; ctime survives re-consent), so the first sighting must admit it doesn't know
    rather than invent a horizon."""
    import evals.preflight.checks as checks
    monkeypatch.setattr(checks, "_OAUTH_SEEN_PATH", tmp_path / ".oauth_seen.json")

    first_seen, is_new = checks._refresh_token_first_seen("1//secret-token-value")
    assert is_new is True
    assert first_seen == pytest.approx(time.time(), abs=5)


def test_second_observation_reuses_the_recorded_timestamp(tmp_path, monkeypatch):
    import evals.preflight.checks as checks
    monkeypatch.setattr(checks, "_OAUTH_SEEN_PATH", tmp_path / ".oauth_seen.json")

    checks._refresh_token_first_seen("1//token-a")
    again, is_new = checks._refresh_token_first_seen("1//token-a")
    assert is_new is False
    assert again == pytest.approx(time.time(), abs=5)


def test_token_rotation_resets_the_clock(tmp_path, monkeypatch):
    """Re-consent issues a NEW refresh token, which must restart the 7-day clock — that's
    the whole reason this is keyed on the token value rather than on the file."""
    import evals.preflight.checks as checks
    path = tmp_path / ".oauth_seen.json"
    monkeypatch.setattr(checks, "_OAUTH_SEEN_PATH", path)

    checks._refresh_token_first_seen("1//old-token")
    # Backdate the old entry to 6 days ago.
    data = json.loads(path.read_text(encoding="utf-8"))
    key = next(iter(data))
    data[key] = time.time() - 6 * 24 * 3600
    path.write_text(json.dumps(data), encoding="utf-8")

    old_seen, old_is_new = checks._refresh_token_first_seen("1//old-token")
    assert old_is_new is False
    assert (time.time() - old_seen) / 86400 == pytest.approx(6, abs=0.1)

    new_seen, new_is_new = checks._refresh_token_first_seen("1//rotated-token")
    assert new_is_new is True
    assert (time.time() - new_seen) < 5


def test_sidecar_never_stores_the_token_itself(tmp_path, monkeypatch):
    import evals.preflight.checks as checks
    path = tmp_path / ".oauth_seen.json"
    monkeypatch.setattr(checks, "_OAUTH_SEEN_PATH", path)

    secret = "1//super-secret-refresh-token"
    checks._refresh_token_first_seen(secret)
    body = path.read_text(encoding="utf-8")
    assert secret not in body
    assert "super-secret" not in body


def test_unwritable_sidecar_does_not_raise(tmp_path, monkeypatch):
    """A best-effort sidecar must never be the thing that blocks a run."""
    import evals.preflight.checks as checks
    # Point at a path whose parent is a FILE, so mkdir/write both fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(checks, "_OAUTH_SEEN_PATH", blocker / "nested" / ".oauth_seen.json")

    first_seen, is_new = checks._refresh_token_first_seen("1//tok")
    assert is_new is True
    assert first_seen == pytest.approx(time.time(), abs=5)


# ── dataset satisfiability ───────────────────────────────────────────────────


def test_unloadable_suite_blocks_rather_than_being_skipped():
    """`--all` used to crash or silently skip a suite that wouldn't parse. Either way the
    run under-reports coverage without saying so."""
    ctx = PreflightContext(cases=[], load_errors={"workflows": "ValueError: unknown expect key"})
    res = check_datasets(ctx)
    assert res.status == FAIL
    assert "workflows" in res.detail


def test_duplicate_case_ids_are_blocking():
    """Duplicate ids collapse in per-case history, silently hiding one case's verdict."""
    ctx = PreflightContext(cases=[Case(id="dup", prompt="a"), Case(id="dup", prompt="b")])
    res = check_datasets(ctx)
    assert res.status == FAIL
    assert "dup" in res.detail


def test_clean_dataset_passes():
    ctx = PreflightContext(cases=[Case(id="a", prompt="x", expect={"tools_include": ["get_datetime"]})])
    res = check_datasets(ctx)
    assert res.status in (OK, WARN)


# ── orchestration ────────────────────────────────────────────────────────────


def test_only_requested_checks_run():
    report = run_preflight(PreflightContext(cases=[]), only=["disk"])
    assert [r.name for r in report.results] == ["disk"]


def test_a_crashing_check_fails_closed_rather_than_passing():
    """A check that raises must never be read as 'dependency healthy' — we don't know the
    state it was meant to verify."""
    import evals.preflight.gate as gate

    def _boom(ctx):
        raise RuntimeError("probe exploded")

    original = gate.CHECKS.get("disk")
    gate.CHECKS["disk"] = _boom
    try:
        report = run_preflight(PreflightContext(cases=[]), only=["disk"])
    finally:
        if original:
            gate.CHECKS["disk"] = original

    assert report.ok is False
    assert report.results[0].status == FAIL
    assert "crashed" in report.results[0].detail


def test_unknown_requirement_names_are_ignored():
    """Adding a capability to the tool map before writing its check must not explode."""
    report = run_preflight(PreflightContext(cases=[]), ["disk", "not_a_real_check"])
    assert {r.name for r in report.results} == {"disk", "datasets"}


def test_report_blocks_only_on_fail_not_warn():
    warn_only = PreflightReport(
        results=[CheckResult("x", WARN, "heads up"), CheckResult("y", OK, "fine")],
        requirements=["x", "y"], elapsed_s=0.1)
    assert warn_only.ok is True
    assert len(warn_only.warnings) == 1

    blocked = PreflightReport(
        results=[CheckResult("x", FAIL, "dead")], requirements=["x"], elapsed_s=0.1)
    assert blocked.ok is False
    assert len(blocked.blocking) == 1


def test_render_is_ascii_only():
    """This prints to a cp1252 Windows console; a non-ASCII char here once crashed the gate
    itself, which is the worst possible failure for a tool whose job is to prevent waste."""
    report = PreflightReport(
        results=[CheckResult("a", FAIL, "it broke", remedy="fix it"),
                 CheckResult("b", WARN, "iffy", remedy="maybe fix"),
                 CheckResult("c", OK, "fine")],
        requirements=["a", "b", "c"], elapsed_s=1.0)
    out = report.render()
    out.encode("cp1252")          # raises UnicodeEncodeError on regression
    assert all(ord(ch) < 128 for ch in out)


def test_manifest_captures_check_facts_for_later_attribution():
    report = PreflightReport(
        results=[CheckResult("disk", OK, "lots", facts={"free_gb": 500.0})],
        requirements=["disk"], elapsed_s=0.2)
    m = report.manifest()
    assert m["checks"]["disk"]["facts"]["free_gb"] == 500.0
    assert m["requirements"] == ["disk"]
