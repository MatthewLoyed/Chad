import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.application.commands.chat_history_commands import list_workspace_projects


def test_list_workspace_projects_discovers_marked_dirs(tmp_path):
    project_dir = tmp_path / "AIAssistant"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()

    with patch("src.application.commands.chat_history_commands.ConfigManager.get") as mock_get:
        mock_get.return_value = {"coding_roots": [str(tmp_path)]}
        projects = list_workspace_projects()

    assert len(projects) == 1
    assert projects[0]["name"] == "AIAssistant"
    assert projects[0]["path"] == str(project_dir.resolve())
