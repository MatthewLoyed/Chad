"""
Shell Commander Tool - Run arbitrary shell commands with safety checks.
"""

import subprocess
import os
import sys
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.tools.display_helpers import format_host_command

logger = setup_logger("ShellCommander")

class ShellCommanderTool(BaseTool):
    """
    Tool for running shell commands on the host machine.
    Uses Git Bash on Windows (or PowerShell fallback) and standard bash on UNIX.
    """
    
    name = "run_host_command"
    description = "Execute shell commands, run git operations, and execute terminal scripts on the host system. Defaults to working in the project root or a specified subdirectory."
    category = "os"
    
    def format_action(self, params: Dict[str, Any]) -> str:
        return format_host_command(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The full shell command to execute (e.g., 'dotnet test Shopping.sln')."
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional: Working directory to run the command in. Defaults to the project root. Paths relative to project root are accepted (e.g., 'sandbox/extracted_project')."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional: Command timeout in seconds. Defaults to 30."
                    }
                },
                "required": ["command"]
            }
        }
    
    async def execute(self, command: str, cwd: Optional[str] = None, timeout: int = 30, **kwargs) -> Dict[str, Any]:
        try:
            # 1. Resolve Work Dir
            if cwd:
                abs_cwd = Path(ConfigManager.expand_path(cwd)).resolve()
            else:
                abs_cwd = Path(os.getcwd()).resolve()

            if not abs_cwd.exists():
                return {"success": False, "error": f"CWD does not exist: {abs_cwd}"}

            logger.info(f"[ SHELL ] : Executing in {abs_cwd}: {command}")

            # 2. Execution Strategy (Prefer Git Bash on Windows)
            if sys.platform == 'win32':
                git_bash_path = Path("C:/Program Files/Git/bin/bash.exe")
                if git_bash_path.exists():
                    shell_executable = str(git_bash_path)
                    cmd_args = [shell_executable, "-c", command]
                else:
                    shell_executable = "powershell.exe"
                    cmd_args = [shell_executable, "-Command", command]
            else:
                shell_executable = "/bin/bash"
                cmd_args = [shell_executable, "-c", command]

            # Ensure project virtualenv is on PATH if present
            cmd_env = os.environ.copy()
            venv_bin = abs_cwd / (".venv/Scripts" if sys.platform == 'win32' else ".venv/bin")
            if venv_bin.exists():
                cmd_env["PATH"] = f"{str(venv_bin)}{os.pathsep}{cmd_env.get('PATH', '')}"
                cmd_env["VIRTUAL_ENV"] = str(abs_cwd / ".venv")

            process = subprocess.run(
                cmd_args,
                cwd=str(abs_cwd),
                env=cmd_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'
            )

            stdout = process.stdout.strip()
            stderr = process.stderr.strip()
            
            success = process.returncode == 0

            return {
                "success": success,
                "result": stdout if success else f"Command failed with exit code {process.returncode}",
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": process.returncode,
                "cwd": str(abs_cwd)
            }

        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {timeout} seconds."}
        except Exception as e:
            logger.error(f"ShellCommander failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
