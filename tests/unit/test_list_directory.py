import os
import pytest
import shutil
import tempfile
from pathlib import Path
from src.tools.filesystem.list_directory import ListDirectoryTool, GitignoreMatcher


@pytest.fixture
def temp_workspace(monkeypatch):
    # Setup temporary directory structure for tests
    original_cwd = os.getcwd()
    temp_dir = Path(tempfile.mkdtemp())

    # ListDirectoryTool._is_safe_path() only allows paths under Path.cwd() or
    # Path.home(). On Windows, the system temp dir happens to live under the
    # user's home directory, so this passed by accident; on Linux CI, /tmp is
    # neither cwd nor $HOME, so the same test failed there (#216). chdir into
    # the fixture's own temp dir so the safety check passes on every platform.
    monkeypatch.chdir(temp_dir)

    # Create sub-folders
    src_dir = temp_dir / "src"
    src_dir.mkdir()
    agents_dir = src_dir / "agents"
    agents_dir.mkdir()
    
    # Create files with different sizes
    (temp_dir / "main.py").write_text("print('hello')", encoding='utf-8')  # ~14 bytes
    (temp_dir / "config.yaml").write_text("caching: false", encoding='utf-8')  # ~14 bytes
    (agents_dir / "supervisor.py").write_text("class Supervisor:\n    pass", encoding='utf-8')  # ~27 bytes
    (agents_dir / "code_agent.py").write_text("print('code')", encoding='utf-8')
    
    # Create hidden file
    (temp_dir / ".env").write_text("TOKEN=secret", encoding='utf-8')
    
    # Create .gitignore
    (temp_dir / ".gitignore").write_text("config.yaml\nsrc/agents/code_agent.py\n", encoding='utf-8')

    yield temp_dir

    # Cleanup — chdir out first: on Windows, rmtree fails with a PermissionError
    # if the process's cwd is still inside the directory being removed.
    os.chdir(original_cwd)
    shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_list_directory_basic(temp_workspace):
    tool = ListDirectoryTool()
    
    # Test depth=1 listing
    result = await tool.execute(path=str(temp_workspace), depth=1, respect_gitignore=False)
    assert result["success"]
    tree_out = result["result"]
    
    # Verify emojis, items count, and file size formatting
    assert "📁 " in tree_out
    assert "📄 main.py" in tree_out
    assert "📁 src/" in tree_out
    assert "(1 items)" in tree_out  # src contains agents/ folder
    # Note: hidden file .env is excluded by default
    assert ".env" not in tree_out


@pytest.mark.asyncio
async def test_list_directory_hidden(temp_workspace):
    tool = ListDirectoryTool()
    
    result = await tool.execute(path=str(temp_workspace), depth=1, include_hidden=True, respect_gitignore=False)
    assert result["success"]
    tree_out = result["result"]
    
    assert "📄 .env" in tree_out


@pytest.mark.asyncio
async def test_list_directory_gitignore(temp_workspace):
    tool = ListDirectoryTool()
    
    # Mock current working directory to temp_workspace so GitignoreMatcher resolves relative to it
    import os
    orig_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = await tool.execute(path=str(temp_workspace), depth=3, respect_gitignore=True)
        assert result["success"]
        tree_out = result["result"]
        
        # config.yaml is in .gitignore, so it should be excluded
        assert "config.yaml" not in tree_out
        # src/agents/code_agent.py is in .gitignore, so it should be excluded
        assert "code_agent.py" not in tree_out
        # supervisor.py is not in .gitignore, so it should be present
        assert "supervisor.py" in tree_out
    finally:
        os.chdir(orig_cwd)


@pytest.mark.asyncio
async def test_list_directory_pattern(temp_workspace):
    tool = ListDirectoryTool()
    
    result = await tool.execute(path=str(temp_workspace), depth=1, pattern="*.yaml", respect_gitignore=False)
    assert result["success"]
    tree_out = result["result"]
    
    # main.py does not match *.yaml, so it should be excluded
    assert "main.py" not in tree_out
    # config.yaml matches, so it should be present
    assert "config.yaml" in tree_out


@pytest.mark.asyncio
async def test_list_directory_typo_suggestion(temp_workspace):
    tool = ListDirectoryTool()
    
    # Try listing non-existent directory with typo 'srcc' instead of 'src'
    bad_path = temp_workspace / "srcc"
    result = await tool.execute(path=str(bad_path))
    assert not result["success"]
    error_out = result["error"]
    
    assert "Did you mean" in error_out
    assert "src" in error_out


@pytest.mark.asyncio
async def test_list_directory_depth_none(temp_workspace):
    tool = ListDirectoryTool()
    
    # Test depth=None listing
    result = await tool.execute(path=str(temp_workspace), depth=None, respect_gitignore=False)
    assert result["success"]
    assert "📁 src/" in result["result"]

