"""A clean install must not borrow another checkout's environment file."""
import os
from pathlib import Path
import runpy
from unittest.mock import Mock


def test_missing_project_env_does_not_search_other_directories(tmp_path, monkeypatch):
    home = tmp_path / "clean-install"
    home.mkdir()
    unrelated = tmp_path / "other-project"
    unrelated.mkdir()
    (unrelated / ".env").write_text("CHAD_TEST_UNRELATED_TOKEN=wrong-project\n")
    monkeypatch.setenv("CHAD_HOME", str(home))
    monkeypatch.chdir(unrelated)
    monkeypatch.delenv("CHAD_TEST_UNRELATED_TOKEN", raising=False)
    load = Mock()
    monkeypatch.setattr("dotenv.load_dotenv", load)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "src/shared/config_manager.py"))
    load.assert_not_called()
    assert "CHAD_TEST_UNRELATED_TOKEN" not in os.environ


def test_project_env_is_explicit_and_launcher_port_is_preserved(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PORT=9999\n")
    monkeypatch.setenv("CHAD_HOME", str(tmp_path))
    monkeypatch.setenv("PORT", "8789")
    load = Mock(side_effect=lambda **kwargs: os.environ.update(PORT="9999"))
    monkeypatch.setattr("dotenv.load_dotenv", load)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "src/shared/config_manager.py"))
    load.assert_called_once_with(dotenv_path=str(tmp_path / ".env"), override=True)
    assert os.environ["PORT"] == "8789"


def test_database_loader_also_uses_only_the_project_env(tmp_path, monkeypatch):
    from src.infrastructure.database import supabase_manager as module
    monkeypatch.setenv("CHAD_HOME", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setattr(module.SupabaseManager, "_instance", None)
    load = Mock()
    monkeypatch.setattr(module, "load_dotenv", load)
    manager = module.SupabaseManager()
    load.assert_called_once_with(dotenv_path=str(tmp_path / ".env"))
    assert manager.enabled is False
