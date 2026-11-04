"""Workspace orientation uses actual project metadata, never guessed commands."""
import importlib.util
from pathlib import Path

_MODULE = Path(__file__).resolve().parents[2] / "docker/openhands/chad_workspace_brief.py"
_SPEC = importlib.util.spec_from_file_location("chad_workspace_brief", _MODULE)
_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_module)


def test_node_metadata_and_original_request(tmp_path, monkeypatch):
    monkeypatch.setattr(_module.shutil, "which", lambda name: "/bin/" + name if name in {"node", "npm"} else None)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"vite build","test":"vitest run","dev":"vite"}}'
    )
    (tmp_path / "pnpm-lock.yaml").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    task = "Implement all requirements.\nDo not deploy."
    result = _module.workspace_brief(task, tmp_path)
    assert result.endswith(task)
    assert str(tmp_path) in result
    assert '"build": "vite build"' in result
    assert '"test": "vitest run"' in result
    assert '"dev"' not in result
    assert "pnpm-lock.yaml" in result
    assert 'Root directories: ["src"]' in result
    assert "Available executables: node, npm" in result
    assert "apply_patch" not in result


def test_polyglot_project_and_malformed_package(tmp_path, monkeypatch):
    monkeypatch.setattr(_module.shutil, "which", lambda name: None)
    for name in ("pyproject.toml", "Cargo.toml", "go.mod", "Demo.csproj"):
        (tmp_path / name).touch()
    (tmp_path / "package.json").write_text("{broken")
    result = _module.workspace_brief("Repair", tmp_path)
    for name in ("pyproject.toml", "Cargo.toml", "go.mod", "Demo.csproj"):
        assert name in result
    assert "could not be read as JSON" in result
    assert result.endswith("Repair")


def test_non_object_scripts_and_bom(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts":null}', encoding="utf-8-sig")
    assert "ORIGINAL USER TASK" in _module.workspace_brief("Fix", tmp_path)
