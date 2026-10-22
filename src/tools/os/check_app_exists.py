"""Read-only installed-app / launch-target lookup on the connected Windows PC."""
from typing import Any, Dict
from src.tools.base_tool import BaseTool
from src.tools.os.app_launcher import ApplicationConfigLookup
from src.remote.daemon_bridge import DaemonExecutorBridge


class CheckAppExistsTool(BaseTool):
    name = "check_app_exists"
    category = "os"
    is_side_effecting = False
    description = (
        "READ: Check whether a named application is installed or has a local launch target, "
        "without launching it. Searches configured paths, PATH and Start Menu shortcuts. "
        "Use for 'is Prism Launcher installed', 'find app executable' or 'verify an app exists'. "
        "Reports executable/shortcut evidence and limitations; does not prove startup works."
    )

    def get_schema(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "annotations": {"readOnlyHint": True},
                "parameters": {"type": "object", "properties": {"name": {
                    "type": "string", "description": "Application name, e.g. Prism Launcher, or an absolute application path."}}, "required": ["name"]}}

    def format_action(self, params: Dict[str, Any]) -> str:
        return f"Checking application: {params.get('name', '')}"

    async def execute(self, name: str, **kwargs) -> Dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            return {"success": False, "error": "A non-empty application name is required."}
        try:
            result = await DaemonExecutorBridge().execute_primitive(
                action="check_app_exists", payload={"name": name.strip(),
                    "explicit_path": ApplicationConfigLookup.find_configured_app(name.strip())}, timeout=10.0)
            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
