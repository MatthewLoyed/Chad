"""Persona-overlay redirection, so eval lanes can run on a fictional persona.

The leak: `evals/memory_write_live.py` swaps the knowledge graph for a fictional-persona
fixture, but the persona OVERLAY (`config/persona/identity.md`, `lessons.md`) still carried
the real user's name into every system prompt. The model resolved the fixture's
first-person prompts to the real person and saved facts under their name — in a lane whose
own header says "Fictional persona only ... never real personal data".

Verified against the real overlay on this machine before the fix: `identity` and `lessons`
both carried the owner's name.
"""
import os

import pytest

from src.infrastructure.repositories import persona_store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(persona_store.PERSONA_OVERLAY_ENV, raising=False)
    persona_store.invalidate_cache() if hasattr(persona_store, "invalidate_cache") else None
    yield


def test_default_overlay_is_the_user_config_dir():
    assert persona_store.overlay_dir().name == "persona"
    assert persona_store.overlay_dir().parent.name == "config"


def test_env_override_redirects_the_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv(persona_store.PERSONA_OVERLAY_ENV, str(tmp_path))
    assert persona_store.overlay_dir() == tmp_path


def test_blank_override_falls_back_to_the_real_overlay(monkeypatch):
    """An empty env var must not silently redirect production to the filesystem root."""
    monkeypatch.setenv(persona_store.PERSONA_OVERLAY_ENV, "   ")
    assert persona_store.overlay_dir().name == "persona"


def test_an_empty_overlay_still_loads_the_full_baseline(tmp_path, monkeypatch):
    """Redirecting must not strip the persona — it should fall through to the baseline.

    `memory_policy.md` matters specifically: the lane's tier-1 consent cases are testing
    exactly that fragment, so a redirect that dropped it would turn a privacy fix into a
    silent eval regression.
    """
    monkeypatch.setenv(persona_store.PERSONA_OVERLAY_ENV, str(tmp_path))
    fragments = persona_store.load_fragments()

    assert "identity" in fragments
    assert "memory_policy" in fragments
    baseline_count = len(list(persona_store.baseline_dir().glob("*.md")))
    assert len(fragments) >= baseline_count - 1  # minus any skipped/retired id


def test_redirected_overlay_carries_no_personal_content(tmp_path, monkeypatch):
    """The actual regression guard.

    Compares against the real overlay rather than a hardcoded name: whatever personal
    strings this machine's overlay contains, none of them may survive the redirect.
    """
    personal_dir = persona_store.overlay_dir()
    if not personal_dir.is_dir() or not list(personal_dir.glob("*.md")):
        pytest.skip("no personal overlay on this machine — nothing to leak")
    personal_bodies = {
        p.stem: p.read_text(encoding="utf-8", errors="ignore")
        for p in personal_dir.glob("*.md")
    }

    monkeypatch.setenv(persona_store.PERSONA_OVERLAY_ENV, str(tmp_path))
    fragments = persona_store.load_fragments()

    for frag_id, personal_text in personal_bodies.items():
        loaded = (fragments.get(frag_id) or ("", ""))[1] or ""
        assert loaded != personal_text, (
            f"persona fragment '{frag_id}' still resolves to the personal overlay "
            f"despite {persona_store.PERSONA_OVERLAY_ENV} pointing elsewhere"
        )


def test_the_lane_sets_the_override_before_building_the_core():
    """A redirect applied after warmup would be a no-op — the registry loads at build."""
    import inspect

    from evals import memory_write_live

    source = inspect.getsource(memory_write_live.run)
    set_at = source.index("os.environ[PERSONA_OVERLAY_ENV]")
    built_at = source.index("build_inprocess_session(")
    assert set_at < built_at, "persona overlay must be redirected before the core is built"


def test_the_lane_restores_the_previous_value():
    import inspect

    from evals import memory_write_live

    source = inspect.getsource(memory_write_live.run)
    assert "prior_persona_env" in source
    assert "os.environ.pop" in source or "os.environ[PERSONA_OVERLAY_ENV] = prior" in source
