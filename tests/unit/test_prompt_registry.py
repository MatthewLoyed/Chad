"""PromptRegistry + persona_store — local file resolution, overlay precedence, versioned writes.

Persona prompts used to resolve Supabase -> disk cache -> bundled defaults; #589 replaced
that chain with two local layers (baseline files + a per-user overlay). These tests cover
the properties that chain used to need: that a fresh install boots on committed baseline
files alone, that a customized install gets its own content, and that a write can't
silently relocate a fragment into every prompt by dropping its scope.
"""
from __future__ import annotations

import pytest

import src.infrastructure.database.local_store as local_store
import src.infrastructure.repositories.persona_store as store
from src.infrastructure.database.local_postgrest import get_local_shim
from src.infrastructure.repositories.prompt_registry import PromptRegistry


@pytest.fixture(autouse=True)
def _isolated_persona_dirs(tmp_path, monkeypatch):
    """Isolate both persona layers AND the history database.

    The shim is a process-wide singleton pointing at the real data/local_chad.db, so
    without redirecting it these tests read and write the user's actual persona history
    — which is how a stray diagnostic write showed up inside a test run.
    """
    baseline = tmp_path / "baseline"
    overlay = tmp_path / "overlay"
    baseline.mkdir()
    overlay.mkdir()
    monkeypatch.setattr(store, "baseline_dir", lambda: baseline)
    monkeypatch.setattr(store, "overlay_dir", lambda: overlay)

    monkeypatch.setattr(local_store, "get_local_db_path", lambda: str(tmp_path / "test.db"))
    shim = get_local_shim()
    shim._created.discard("persona_history")

    PromptRegistry._instance = None
    yield baseline, overlay
    PromptRegistry._instance = None
    shim._created.discard("persona_history")


def _write(path, frag_id, scope, content):
    body = f"---\nscope: {scope}\n---\n\n{content}\n" if scope else f"{content}\n"
    (path / f"{frag_id}.md").write_text(body, encoding="utf-8")


# --- resolution -------------------------------------------------------------


def test_baseline_only_resolves(_isolated_persona_dirs):
    """A fresh clone has no overlay at all and must still produce a full persona."""
    baseline, _ = _isolated_persona_dirs
    _write(baseline, "identity", "always", "Generic Chad.")

    assert store.load_fragments() == {"identity": ("always", "Generic Chad.")}


def test_overlay_overrides_baseline(_isolated_persona_dirs):
    baseline, overlay = _isolated_persona_dirs
    _write(baseline, "identity", "always", "Generic Chad.")
    _write(overlay, "identity", "always", "Alex's Chad.")

    assert store.load_fragments()["identity"] == ("always", "Alex's Chad.")


def test_overlay_without_scope_inherits_baseline_scope(_isolated_persona_dirs):
    """Editing a prompt's prose must not silently change where it gets injected."""
    baseline, overlay = _isolated_persona_dirs
    _write(baseline, "planner", "planner", "Plan carefully.")
    _write(overlay, "planner", None, "Plan very carefully.")

    scope, content = store.load_fragments()["planner"]
    assert scope == "planner", "scope must be inherited, not reset to the 'always' default"
    assert content == "Plan very carefully."


def test_baseline_survives_when_overlay_covers_only_some_ids(_isolated_persona_dirs):
    baseline, overlay = _isolated_persona_dirs
    _write(baseline, "identity", "always", "Generic identity.")
    _write(baseline, "soul", "always", "Generic soul.")
    _write(overlay, "soul", "always", "Custom soul.")

    fragments = store.load_fragments()
    assert fragments["identity"] == ("always", "Generic identity.")
    assert fragments["soul"] == ("always", "Custom soul.")


def test_empty_overlay_fragment_does_not_shadow_baseline(_isolated_persona_dirs):
    """The live Supabase `user` row had been blanked to a single space; an empty
    override must fall through rather than silently erase a prompt."""
    baseline, overlay = _isolated_persona_dirs
    _write(baseline, "identity", "always", "Generic identity.")
    _write(overlay, "identity", "always", "   ")

    assert store.load_fragments()["identity"] == ("always", "Generic identity.")


def test_deprecated_ids_are_never_loaded(_isolated_persona_dirs):
    baseline, _ = _isolated_persona_dirs
    _write(baseline, "user", "always", "Name: somebody")
    _write(baseline, "identity", "always", "Generic Chad.")

    assert "user" not in store.load_fragments()


def test_file_without_frontmatter_reads_as_body(_isolated_persona_dirs):
    baseline, _ = _isolated_persona_dirs
    (baseline / "identity.md").write_text("Just prose, no frontmatter.", encoding="utf-8")

    assert store.load_fragments()["identity"] == ("always", "Just prose, no frontmatter.")


# --- writes -----------------------------------------------------------------


def test_write_lands_in_overlay_not_baseline(_isolated_persona_dirs):
    """The baseline is published to other people and must not absorb one user's edits."""
    baseline, overlay = _isolated_persona_dirs
    _write(baseline, "tools", "supervisor", "Generic tool rules.")

    assert store.write_fragment("tools", "Learned tool rule.") is True
    assert (overlay / "tools.md").exists()
    assert "Generic tool rules." in (baseline / "tools.md").read_text(encoding="utf-8")


def test_write_carries_scope_forward(_isolated_persona_dirs):
    baseline, _ = _isolated_persona_dirs
    _write(baseline, "tools", "supervisor", "Generic tool rules.")

    store.write_fragment("tools", "Learned tool rule.")

    assert store.load_fragments()["tools"][0] == "supervisor"


def test_write_refuses_empty_content(_isolated_persona_dirs):
    assert store.write_fragment("tools", "   ") is False


def test_write_refuses_deprecated_id(_isolated_persona_dirs):
    assert store.write_fragment("user", "Name: somebody") is False


def test_write_records_incrementing_history(_isolated_persona_dirs):
    store.write_fragment("tools", "First.", scope="supervisor")
    store.write_fragment("tools", "Second.", scope="supervisor")

    versions = [h["version"] for h in store.history("tools")]
    assert versions == [2, 1], "history is newest-first with incrementing versions"


def test_rollback_restores_earlier_content(_isolated_persona_dirs):
    store.write_fragment("tools", "Good rule.", scope="supervisor")
    store.write_fragment("tools", "Bad auto-written rule.", scope="supervisor")
    assert store.load_fragments()["tools"][1] == "Bad auto-written rule."

    assert store.rollback("tools", 1) is True

    scope, content = store.load_fragments()["tools"]
    assert content == "Good rule."
    assert scope == "supervisor"
    assert store.history("tools")[0]["version"] == 3, "rollback is recorded, not a rewind"


def test_rollback_to_unknown_version_fails(_isolated_persona_dirs):
    store.write_fragment("tools", "Only version.", scope="supervisor")
    assert store.rollback("tools", 99) is False


# --- registry ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_warmup_loads_effective_persona(_isolated_persona_dirs):
    baseline, overlay = _isolated_persona_dirs
    _write(baseline, "identity", "always", "Generic Chad.")
    _write(baseline, "planner", "planner", "Plan.")
    _write(overlay, "identity", "always", "Alex's Chad.")

    reg = PromptRegistry.get_instance()
    await reg.warmup()

    assert reg.get_prompt("identity") == "Alex's Chad."
    assert reg.get_prompt("planner") == "Plan."
    assert reg.get_prompt_scope("planner") == "planner"


@pytest.mark.asyncio
async def test_warmup_raises_when_no_fragments_exist(_isolated_persona_dirs):
    """No baseline means a broken install, not a degraded-but-usable one."""
    reg = PromptRegistry.get_instance()
    with pytest.raises(RuntimeError, match="no persona fragments"):
        await reg.warmup()


@pytest.mark.asyncio
async def test_warmup_does_not_touch_supabase(_isolated_persona_dirs, monkeypatch):
    """Regression guard for #589: prompts must resolve with no network/DB dependency."""
    baseline, _ = _isolated_persona_dirs
    _write(baseline, "identity", "always", "Generic Chad.")

    def _explode(*a, **k):
        raise AssertionError("warmup must not construct a Supabase client")

    monkeypatch.setattr(
        "src.infrastructure.database.supabase_manager.get_supabase_manager", _explode
    )

    reg = PromptRegistry.get_instance()
    await reg.warmup()
    assert reg.get_prompt("identity") == "Generic Chad."


def test_get_prompt_raises_before_warmup(_isolated_persona_dirs):
    reg = PromptRegistry.get_instance()
    with pytest.raises(RuntimeError, match="not warmed up"):
        reg.get_prompt("anything")


@pytest.mark.asyncio
async def test_get_prompts_by_scopes_filters_correctly(_isolated_persona_dirs):
    baseline, _ = _isolated_persona_dirs
    _write(baseline, "a", "supervisor", "A")
    _write(baseline, "b", "planner", "B")
    _write(baseline, "c", "supervisor", "C")

    reg = PromptRegistry.get_instance()
    await reg.warmup()

    assert set(reg.get_prompts_by_scopes(["supervisor"]).keys()) == {"a", "c"}


def test_deep_research_agent_prompt_file_exists():
    from pathlib import Path
    p = Path("prompts/persona/deep_research_agent.md")
    assert p.is_file(), "deep_research_agent.md must exist in prompts/persona/"
    content = p.read_text(encoding="utf-8")
    assert "scope: deep_research_agent" in content
    assert "deep research specialist" in content

