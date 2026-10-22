"""
System Monitor Tool - Visibility into Cron and Heartbeat services.
"""

from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("SystemMonitorTool")

class SystemMonitorTool(BaseTool):
    """Tool for monitoring system services status."""

    name = "system_monitor"
    description = """Monitor the status of background services like Cron and Heartbeat.
Check scheduled tasks, last run results, and system health."""
    category = "system"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._server = None

    def _get_server(self):
        """Lazy-load server instance."""
        if self._server is None:
            from src.remote.server import ChadServer
            # This tool expects to be running within the server context or have access to it
            # In Chad's architecture, we can usually find it via the registry or singleton patterns if implemented
            # For now, we'll try to find the global server if it's been set somewhere accessible
            pass
        return self._server

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "cron_list", "heartbeat_last"],
                        "description": "Information to retrieve: 'status' for summary, 'cron_list' for all tasks, 'heartbeat_last' for last run details."
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Optional ID to get detailed info about a specific cron job."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(self, action: str, job_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            # In Chad's architecture, we can try to find the server/services via global registry
            # Or by importing the server module if it stores instances.
            # For this MVP, we'll try to find the orchestrator which is usually accessible.
            
            # Since I'm running in the context of the assistant, I'll try to find the services
            # via the server's static or singleton access if I add one, or more simply
            # by looking for them in the known locations.
            
            # For now, I'll implement a robust status reporter that tells the user 
            # if the services are found and their state.
            
            # Note: To make this truly work, I'll add a class variable to server to hold the instance
            from src.remote.server import ChadServer
            server = getattr(ChadServer, 'instance', None)
            
            if not server:
                return {
                    "success": False, 
                    "error": "Server instance not found. Are you running in headless mode?"
                }
            
            if action == "status":
                cron_status = server._cron_scheduler.get_status() if server._cron_scheduler else "offline"
                hb_status = server._heartbeat_service.get_status() if server._heartbeat_service else "offline"
                
                cron_label = cron_status.get("status", "unknown") if isinstance(cron_status, dict) else cron_status
                hb_label = hb_status.get("status", "unknown") if isinstance(hb_status, dict) else hb_status
                
                result_text = f"System Status:\n- Cron: {cron_label}\n- Heartbeat: {hb_label}"
                return {"success": True, "result": result_text, "status": {"cron": cron_status, "heartbeat": hb_status}}
                
            elif action == "cron_list":
                if not server._cron_scheduler:
                    return {"success": False, "error": "Cron scheduler not running."}
                status = server._cron_scheduler.get_status()
                tasks = status.get("tasks", [])
                
                if not tasks:
                    return {"success": True, "result": "No tasks scheduled."}
                
                res = "Scheduled tasks:\n"
                for t in tasks:
                    res += f"- {t['name']} (ID: {t['id'][:8]}...): Next at {t['next_run']}\n"
                return {"success": True, "result": res, "tasks": tasks}
                
            elif action == "heartbeat_last":
                if not server._heartbeat_service:
                    return {"success": False, "error": "Heartbeat service not running."}
                status = server._heartbeat_service.get_status()
                return {"success": True, "result": f"Last Heartbeat: {status['last_check']}", "status": status}
            
            return {"success": False, "error": f"Unknown action: {action}"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
