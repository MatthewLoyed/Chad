"""Parity accepts shipped consumers while still rejecting broken scopes."""
import pytest

from evals import persona_parity


@pytest.fixture
def fragments(monkeypatch, tmp_path):
    data = {}
    store = persona_parity.persona_store
    monkeypatch.setattr(store, "baseline_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "overlay_dir", lambda: tmp_path / "overlay")
    monkeypatch.setattr(store, "_read_dir", lambda p: data if p == tmp_path else {})
    monkeypatch.setattr(store, "load_fragments", lambda: data)
    return data


def test_registered_consumers_pass(fragments):
    for scope in ("voice", "telegram", "canvas_agent", "deep_research_agent", "event"):
        fragments[scope] = (scope, "Fictional generic instructions")
    assert persona_parity.run_persona_parity()[0]


def test_unknown_scope_still_blocks(fragments):
    fragments["broken"] = ("misspelled_scope", "Fictional instructions")
    passed, report = persona_parity.run_persona_parity()
    assert not passed
    assert "misspelled_scope" in report
