import base64
import copy
import shutil
import subprocess

import pytest

from evals.chad_persona_capture import (
    snapshot_persona, require_matching_persona, require_effective_scope,
    capture_persona_changes, validate_snapshot,
)
from evals.chad_on_chad_driver import capture_patch


@pytest.fixture
def persona(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAD_PERSONA_LAYER", raising=False)
    monkeypatch.delenv("CHAD_PERSONA_OVERLAY_DIR", raising=False)
    (tmp_path / "prompts/persona").mkdir(parents=True)
    (tmp_path / "config/persona").mkdir(parents=True)
    (tmp_path / "prompts/persona/routing.md").write_text("---\nscope: supervisor\n---\nGeneric routing\n")
    (tmp_path / "config/persona/routing.md").write_bytes(b"Custom routing\n")
    (tmp_path / "config/persona/private.md").write_text("Do not capture personal text\n")
    return tmp_path


def test_effective_overlay_scope_hash_and_privacy(persona):
    snapshot = snapshot_persona(persona, ["routing"])
    fragment = snapshot["fragments"]["routing"]
    assert fragment["effective_path"] == "config/persona/routing.md"
    assert fragment["effective_scope"] == "supervisor"
    assert base64.b64decode(fragment["overlay"]["content_base64"]) == b"Custom routing\n"
    assert "private" not in snapshot["fragments"]
    assert len(snapshot["overlay_inventory"]["config/persona/private.md"]) == 64
    validate_snapshot(snapshot)
    require_matching_persona(persona, snapshot)
    with pytest.raises(ValueError, match="active persona overlay"):
        require_effective_scope(snapshot, ["prompts/persona/routing.md"])


@pytest.mark.parametrize("initial,updated", [(None, "Added overlay"), ("Custom routing\n", None),
                                             ("Custom routing\n", "Changed overlay"), ("Custom routing\n", "")])
def test_capture_add_edit_delete_and_empty_fallback(persona, initial, updated):
    path = persona / "config/persona/routing.md"
    if initial is None:
        path.unlink()
    else:
        path.write_text(initial)
    before = snapshot_persona(persona, ["routing"])
    if updated is None:
        path.unlink()
    else:
        path.write_text(updated)
    changes = capture_persona_changes(persona, before)
    assert changes["changed"]
    after = changes["fragments"]["routing"]["after"]
    assert after["effective_path"] == ("config/persona/routing.md" if updated else "prompts/persona/routing.md")
    with pytest.raises(ValueError, match="drifted"):
        require_matching_persona(persona, before)


@pytest.mark.parametrize("name", ["routing", "private", "new_private"])
def test_unapproved_overlay_change_is_rejected(persona, name):
    before = snapshot_persona(persona, ["routing"])
    path = persona / f"config/persona/{name}.md"
    path.write_text("Changed")
    if name == "routing":
        assert capture_persona_changes(persona, before)["changed"]
    else:
        with pytest.raises(ValueError, match="Unallowlisted"):
            capture_persona_changes(persona, before)


@pytest.mark.parametrize("ids", [[], ["../private"], ["user"], ["routing", "routing"], ["missing"], ["C:/private"]])
def test_invalid_ids_fail_closed(persona, ids):
    with pytest.raises(ValueError):
        snapshot_persona(persona, ids)


def test_tampered_snapshot_rejected(persona):
    before = snapshot_persona(persona, ["routing"])
    bad = copy.deepcopy(before)
    bad["fragments"]["routing"]["overlay"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        capture_persona_changes(persona, bad)


@pytest.mark.parametrize("variable,value", [("CHAD_PERSONA_LAYER", "baseline"),
                                           ("CHAD_PERSONA_OVERLAY_DIR", "elsewhere")])
def test_unsupported_environment_cannot_silently_measure_other_persona(persona, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        snapshot_persona(persona, ["routing"])


def test_link_escape_rejected(persona, tmp_path):
    path = persona / "config/persona/routing.md"
    path.unlink()
    try:
        path.symlink_to(tmp_path / "outside.md")
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(ValueError, match="link"):
        snapshot_persona(persona, ["routing"])


def test_ignored_overlay_only_patch_is_real_and_recoverable(persona):
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    def run(*args):
        return subprocess.run([git, *args], cwd=persona, check=True, capture_output=True)
    (persona / ".gitignore").write_text("config/persona/\n")
    run("init")
    run("add", ".")
    run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    before = snapshot_persona(persona, ["routing"])
    assert not capture_patch(persona, persona_snapshot=before)["changed"]
    (persona / "config/persona/routing.md").write_bytes(b"Candidate routing\n")
    result = capture_patch(persona, persona_snapshot=before)
    assert result["changed"] and not result["diff"] and not result["new_files_base64"]
    fragment = result["persona_changes"]["fragments"]["routing"]
    assert base64.b64decode(fragment["before"]["overlay"]["content_base64"]) == b"Custom routing\n"
    assert base64.b64decode(fragment["after"]["overlay"]["content_base64"]) == b"Candidate routing\n"


def test_provisioned_clone_matches_selected_snapshot(persona, tmp_path, monkeypatch):
    from src.tools.dev.eval_clone_provisioner import provision_eval_clone, deprovision_eval_clone
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    def run(*args):
        return subprocess.check_output([git, *args], cwd=persona)
    (persona / '.gitignore').write_text('config/persona/\n')
    run('init', '-q')
    run('add', '.')
    run('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline')
    sha = run('rev-parse', 'HEAD').decode().strip()
    before = snapshot_persona(persona, ['routing'])
    clone = provision_eval_clone(sha, job_id='snapshot-test', source_repo=persona,
                                 clones_root=tmp_path / 'clones')
    assert clone['success']
    try:
        from pathlib import Path
        require_matching_persona(Path(clone['path']), before)
    finally:
        deprovision_eval_clone(clone['path'], source_repo=persona)
