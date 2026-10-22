"""
Script Runner Tool - Run local scripts with fuzzy file matching
"""

import subprocess
import os
import sys
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from rapidfuzz import process, fuzz

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.display_helpers import format_run_script

logger = setup_logger("ScriptRunner")

class ScriptRunner(BaseTool):
    """Tool for running local scripts (.vbs, .bat, .ps1) with fuzzy matching."""
    
    name = "run_script"
    description = "Run a local script file (.vbs, .bat, .ps1) using fuzzy matching. Ideal for utility scripts."
    category = "os"
    
    SCRIPT_EXTENSIONS = ['.vbs', '.bat', '.ps1', '.cmd']
    AUTO_EXECUTE_THRESHOLD = 70.0
    CONFIRM_THRESHOLD = 50.0
    # Optional per-user aliases: {"nickname": "script_file.bat"}. Configure your
    # own under `script_runner.nickname_map` in config.yaml. Empty by default.
    NICKNAME_MAP: Dict[str, str] = {}

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._load_config()
    
    def _load_config(self) -> None:
        try:
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    full_config = yaml.safe_load(f)
                script_config = full_config.get('script_runner', {})
                scripts_folder = script_config.get('scripts_folder')
                self.SCRIPTS_FOLDER = os.path.expandvars(scripts_folder) if scripts_folder else os.path.expandvars(r"%USERPROFILE%\Desktop")
                self.AUTO_EXECUTE_THRESHOLD = script_config.get('auto_execute_threshold', self.AUTO_EXECUTE_THRESHOLD)
                self.CONFIRM_THRESHOLD = script_config.get('confirm_threshold', self.CONFIRM_THRESHOLD)
                self.NICKNAME_MAP = script_config.get('nickname_map', self.NICKNAME_MAP)
            else:
                self.SCRIPTS_FOLDER = os.path.expandvars(r"%USERPROFILE%\Desktop")
        except Exception as e:
            logger.error(f"Failed to load script runner config: {e}")
            self.SCRIPTS_FOLDER = os.path.expandvars(r"%USERPROFILE%\Desktop")
    
    def _scan_scripts_folder(self) -> List[str]:
        scripts = []
        scripts_path = Path(self.SCRIPTS_FOLDER)
        if not scripts_path.exists(): return scripts
        for ext in self.SCRIPT_EXTENSIONS:
            for script_file in scripts_path.glob(f"*{ext}"):
                scripts.append(script_file.name)
        return scripts
    
    def _find_script(self, user_input: str) -> tuple[Optional[str], float, str]:
        user_input_lower = user_input.lower().strip()
        if user_input_lower in self.NICKNAME_MAP:
            return self.NICKNAME_MAP[user_input_lower], 100.0, 'auto_execute'
        available_scripts = self._scan_scripts_folder()
        if not available_scripts: return None, 0.0, 'not_found'
        result = process.extractOne(user_input_lower, available_scripts, scorer=fuzz.WRatio, score_cutoff=self.CONFIRM_THRESHOLD)
        if not result: return None, 0.0, 'not_found'
        matched_file, score, _ = result
        status = 'auto_execute' if score >= self.AUTO_EXECUTE_THRESHOLD else 'needs_confirmation'
        return matched_file, score, status
    
    def _execute_script(self, script_filename: str) -> Dict[str, Any]:
        script_path = Path(script_filename) if os.path.isabs(script_filename) else Path(self.SCRIPTS_FOLDER) / script_filename
        if not script_path.exists(): return {"success": False, "error": f"Script not found: {script_path}"}
        try:
            logger.info(f"[ SCRIPT ] : Executing {script_filename}...")
            ext = script_path.suffix.lower()
            if ext == '.vbs': cmd = ['wscript.exe', str(script_path)]
            elif ext in ['.bat', '.cmd']: cmd = [str(script_path)]
            elif ext == '.ps1': cmd = ['powershell.exe', '-ExecutionPolicy', 'Bypass', '-File', str(script_path)]
            else: cmd = [str(script_path)]

            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            return {"success": True, "result": f"Executed {script_filename}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def format_action(self, params: Dict[str, Any]) -> str:
        return format_run_script(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "script_name": {
                        "type": "string",
                        "description": "Name or keyword for the script to run."
                    }
                },
                "required": ["script_name"]
            }
        }
    
    async def execute(self, script_name: str, **kwargs) -> Dict[str, Any]:
        matched_file, score, status = self._find_script(script_name)
        if status == 'not_found':
            return {"success": False, "error": f"Script '{script_name}' not found."}
        elif status == 'needs_confirmation':
            return {"success": False, "needs_confirmation": True, "suggested_script": matched_file, "confidence": score}
        else:
            return self._execute_script(matched_file)
