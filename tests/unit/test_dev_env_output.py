"""The shared-profile bootstrap must never echo collected environment lines."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_existing_db_path_does_not_echo_environment(tmp_path):
    db = tmp_path / "fictional.db"
    db.touch()
    env = tmp_path / "fictional.env"
    env.write_text(f"FICTIONAL_SECRET=never-print-this\nCHAD_LOCAL_DB_PATH={db}\nOTHER=value\n")
    script = Path("scripts/prod/setup_dev_worktree.ps1").resolve()
    command = r"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$null, [ref]$null)
$function = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Ensure-EnvLocalDbPath'}, $true)
Invoke-Expression $function.Extent.Text
Ensure-EnvLocalDbPath $args[1] $args[2]
"""
    runner = tmp_path / "check.ps1"
    runner.write_text(command)
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-File", str(runner), str(script), str(env), str(db)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "never-print-this" not in result.stdout + result.stderr
    assert "FICTIONAL_SECRET" not in result.stdout + result.stderr
