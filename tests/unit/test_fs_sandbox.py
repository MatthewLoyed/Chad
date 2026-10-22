"""Filesystem sandbox for eval harnesses (#566).

The bug: `coder_bench` sets up a disposable git worktree and claims isolation, but
`DiscoveryEngine` roots itself at %USERPROFILE% and `fuzzy_find_local` auto-searches
Documents/Desktop/Downloads — so a bench turn found and read a file from the LIVE
production repo instead of its own checkout.
"""
import os

import pytest

from src.shared.fs_sandbox import ENV_VAR, fs_sandbox, is_active, sandbox_root


@pytest.fixture(autouse=True)
def _no_ambient_sandbox(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_unset_means_no_sandbox():
    assert sandbox_root() is None
    assert is_active() is False


def test_set_to_a_real_directory_activates(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    assert sandbox_root() == tmp_path.resolve()
    assert is_active() is True


def test_a_nonexistent_path_is_ignored_rather_than_confining_to_nothing(monkeypatch):
    """A typo must not silently turn every file lookup into "found nothing"."""
    monkeypatch.setenv(ENV_VAR, r"C:\definitely\not\a\real\path\zzz")
    assert sandbox_root() is None


def test_blank_value_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "   ")
    assert sandbox_root() is None


def test_context_manager_restores_the_previous_value(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    outer.mkdir()
    monkeypatch.setenv(ENV_VAR, str(outer))

    inner = tmp_path / "inner"
    inner.mkdir()
    with fs_sandbox(inner):
        assert sandbox_root() == inner.resolve()
    assert sandbox_root() == outer.resolve()


def test_context_manager_restores_on_exception(tmp_path):
    with pytest.raises(ValueError):
        with fs_sandbox(tmp_path):
            raise ValueError("boom")
    assert os.environ.get(ENV_VAR) is None


# --- the actual isolation hole ---------------------------------------------- #


def test_discovery_engine_root_follows_the_sandbox(tmp_path, monkeypatch):
    """The engine is a singleton built at import, so the root must be read per call."""
    from src.core.filesystem.discovery_engine import DiscoveryEngine

    engine = DiscoveryEngine()
    real_root = engine.root_path

    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    assert engine.root_path == tmp_path.resolve()

    monkeypatch.delenv(ENV_VAR)
    assert engine.root_path == real_root


def test_a_sandboxed_find_cannot_reach_outside_the_sandbox(tmp_path, monkeypatch):
    """The #566 symptom, reproduced: a same-named file inside and outside the sandbox."""
    from src.core.filesystem.discovery_engine import DiscoveryEngine

    sandbox = tmp_path / "worktree"
    (sandbox / "src").mkdir(parents=True)
    (sandbox / "src" / "target_module.py").write_text("# the disposable checkout\n")

    outside = tmp_path / "real_repo"
    outside.mkdir()
    (outside / "target_module.py").write_text("# the LIVE production repo\n")

    engine = DiscoveryEngine()
    monkeypatch.setenv(ENV_VAR, str(sandbox))
    matches = engine.find("target_module", "")

    assert matches, "sandboxed find should still locate files inside the sandbox"
    # Assert on CONTENT, not on the path shape: `rel_path` is relative to whatever the
    # engine currently considers its root, so a path-prefix check is satisfied trivially
    # by a wrong root and would pass against the very bug this covers.
    bodies = [(sandbox / m["rel_path"]).read_text() for m in matches]
    assert bodies == ["# the disposable checkout\n"], (
        f"sandboxed find reached outside its root — matched {[m['rel_path'] for m in matches]}"
    )
    assert not any("LIVE production" in b for b in bodies)


async def test_fuzzy_find_auto_roots_become_the_sandbox_not_documents(tmp_path, monkeypatch):
    """A repo checkout has no Documents/Desktop/Downloads — auto-search must not go there."""
    from src.tools.filesystem.discovery_tools import FuzzyFindTool

    (tmp_path / "notes_about_physics.md").write_text("in the sandbox\n")
    monkeypatch.setenv(ENV_VAR, str(tmp_path))

    result = await FuzzyFindTool().execute(query="notes_about_physics")

    assert result["success"] is True, result
    assert any("notes_about_physics" in m["name"] for m in result["matches"])


async def test_read_file_resolves_relative_paths_against_the_sandbox(tmp_path, monkeypatch):
    """`fuzzy_find_local` returns paths relative to the ACTIVE root, so `read_file` must
    use the same base — otherwise a sandboxed find hands back a path that read_file
    resolves against the real home directory."""
    from src.tools.filesystem.discovery_tools import ReadLocalFileTool

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("# the disposable checkout\n")
    monkeypatch.setenv(ENV_VAR, str(tmp_path))

    result = await ReadLocalFileTool().execute(file_path="src/mod.py")

    assert result["success"] is True, result
    assert "disposable checkout" in result["content"]


async def test_read_file_refuses_an_absolute_path_outside_the_sandbox(tmp_path, monkeypatch):
    from src.tools.filesystem.discovery_tools import ReadLocalFileTool

    outside = tmp_path / "real_repo"
    outside.mkdir()
    secret = outside / "settings.py"
    secret.write_text("# the LIVE production repo\n")

    sandbox = tmp_path / "worktree"
    sandbox.mkdir()
    monkeypatch.setenv(ENV_VAR, str(sandbox))

    result = await ReadLocalFileTool().execute(file_path=str(secret))

    assert result["success"] is False
    assert "outside the sandbox" in result["error"]


async def test_read_file_is_unrestricted_when_no_sandbox_is_set(tmp_path, monkeypatch):
    """Production behaviour must be untouched — the sandbox is opt-in."""
    from src.tools.filesystem.discovery_tools import ReadLocalFileTool

    target = tmp_path / "notes.md"
    target.write_text("plain old file\n")

    result = await ReadLocalFileTool().execute(file_path=str(target))

    assert result["success"] is True, result
    assert "plain old file" in result["content"]
