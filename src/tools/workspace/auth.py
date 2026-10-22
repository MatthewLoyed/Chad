"""
Google Workspace Authentication Tools
"""

import asyncio
from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.workspace.manager import GoogleWorkspaceManager

logger = setup_logger("GoogleWorkspace")

class RefreshGoogleAuthTool(BaseTool):
    """Tool to refresh Google OAuth credentials when they expire or are missing."""
    
    name = "refresh_google_auth"
    description = (
        "Refresh Google Workspace/Gmail credentials. "
        "Use this if other workspace tools (gmail, calendar, etc.) fail with authentication errors. "
        "First tries existing credentials and automatic token refresh. If Google rejects "
        "the saved authorization, opens sign-in for the user to approve access."
    )
    category = "workspace"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The reason why authentication is being requested (e.g., 'Gmail token expired')."
                    }
                },
                "required": ["reason"]
            }
        }
    
    async def execute(self, reason: str, **kwargs) -> Dict[str, Any]:
        logger.info(f"Triggering Google Auth Refresh. Reason: {reason}")
        
        try:
            ready, error = await asyncio.to_thread(self._manager.authenticate)
            if ready:
                return {'success': True, 'result': 'Google credentials are valid; automatic refresh completed if needed.'}
            # run_local_auth_flow() blocks synchronously until the browser callback
            # arrives (or the user abandons it). Run it off-thread so it doesn't
            # freeze this process's asyncio event loop for the whole interaction.
            success = await asyncio.to_thread(self._manager.run_local_auth_flow)
            
            if success:
                return {
                    "success": True,
                    "result": "Successfully authenticated with Google. Credentials have been updated and saved."
                }
            else:
                return {
                    "success": False,
                    "error": "Authentication failed or was cancelled by the user."
                }
                
        except Exception as e:
            logger.error(f"Error during Google Auth refresh: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"An error occurred during authentication: {str(e)}"
            }
