"""Exercise installer staging without building binaries or copying personal files."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.asyncio
async def test_copied_environment_template_keeps_chat_history_local(tmp_path, monkeypatch):
    from dotenv import dotenv_values
    from src.infrastructure.database import local_store, supabase_manager
    from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

    template = Path(__file__).resolve().parents[2] / '.env.example'
    shutil.copyfile(template, tmp_path / '.env')
    values = dotenv_values(tmp_path / '.env')
    for key in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_KEY',
                'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_DB_URL', 'CHAD_REMOTE_TOKEN'):
        monkeypatch.delenv(key, raising=False)
        assert not values.get(key), f'{key} must require deliberate configuration'
    monkeypatch.setattr(supabase_manager, 'get_project_root', lambda: tmp_path)
    monkeypatch.setattr(supabase_manager.SupabaseManager, '_instance', None)
    monkeypatch.setenv('CHAD_LOCAL_DB_PATH', str(tmp_path / 'local-chat.db'))
    monkeypatch.setattr(local_store, '_DB_PATH', None)
    repository = ChatHistoryRepository()
    assert repository.supabase.enabled is False
    conversation = await repository.create_conversation(title='First local chat')
    await repository.append_message(conversation['id'], 'user', 'Hello')
    assert (await repository.get_messages(conversation['id']))[0]['content'] == 'Hello'


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer staging is Windows-only")
def test_staged_install_has_persona_and_skills_without_personal_overlays(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = tmp_path / "scripts/prod/stage_installer_payload.ps1"
    script.parent.mkdir(parents=True)
    shutil.copyfile(repo / "scripts/prod/stage_installer_payload.ps1", script)
    setup_helper = tmp_path / "scripts/setup/setup_lmstudio.py"
    setup_helper.parent.mkdir(parents=True)
    shutil.copyfile(repo / "scripts/setup/setup_lmstudio.py", setup_helper)
    for name in ("Chad", "Chad-Brain"):
        app = tmp_path / "dist" / name
        app.mkdir(parents=True)
        (app / f"{name}.exe").write_bytes(b"test executable")
        (app / "chad_home.txt").write_text("private-build-machine")
    shutil.copytree(repo / "config", tmp_path / "config",
                    ignore=shutil.ignore_patterns("persona", "secrets.yaml", "preferences.yaml"))
    for name in ("LICENSE", ".env.example", "CLAUDE.md.example"):
        shutil.copyfile(repo / name, tmp_path / name)
    baseline = tmp_path / "prompts/persona/identity.md"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("Generic identity", encoding="utf-8")
    skill = tmp_path / "src/skills/pdf/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Public skill", encoding="utf-8")
    tool = tmp_path / "src/tools/web/example.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# Public tool discovered from disk\n", encoding="utf-8")
    git = shutil.which("git")
    assert git, "Git is required by installer staging"
    subprocess.run([git, "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(tmp_path), "add", "prompts", "src/skills", "src/tools", "scripts/setup"],
                   check=True, capture_output=True)
    (baseline.parent / "private-untracked.md").write_text("private")
    (skill.parent / "private-untracked.md").write_text("private")
    overlay = tmp_path / "config/persona"
    overlay.mkdir()
    (overlay / "identity.md").write_text("Personal overlay")
    (tmp_path / "config/secrets.yaml").write_text("PRIVATE")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "PowerShell is required by installer staging"
    subprocess.run([shell, "-NoProfile", "-File", str(script)],
                   check=True, capture_output=True, text=True)
    payload = tmp_path / "dist/installer_payload"
    assert (payload / "prompts/persona/identity.md").read_text() == "Generic identity"
    assert (payload / "src/skills/pdf/SKILL.md").read_text() == "Public skill"
    assert (payload / "src/tools/web/example.py").is_file()
    assert (payload / "scripts/setup/setup_lmstudio.py").is_file()
    assert (payload / "LICENSE").is_file()
    assert not (payload / "config/persona").exists()
    assert not (payload / "config/secrets.yaml").exists()
    assert not list(payload.rglob("private-untracked.md"))
    assert not list(payload.rglob("chad_home.txt"))
