from typing import Optional, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class WorkspaceFacadeMixin:
    """Mixin for Google Workspace, Drive, and system dashboard facade methods."""

    def list_drive_files(self, query: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """List or search files in user's Google Drive."""
        try:
            from src.tools.workspace.manager import GoogleWorkspaceManager
            mgr = GoogleWorkspaceManager()
            svc = mgr.get_drive_service()
            if not svc:
                return {"success": False, "error": "Service unavailable"}
            
            q = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"
            if query:
                q += f" and (name contains '{query}' or fullText contains '{query}')"
            
            res = svc.files().list(
                q=q,
                pageSize=limit,
                fields="files(id, name, mimeType, modifiedTime, size, iconLink, webViewLink)",
                orderBy="modifiedTime desc"
            ).execute()
            
            return {"success": True, "files": res.get('files', [])}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_drive_file_content(self, file_id: str, mime_type: str) -> Dict[str, Any]:
        """Download / Export Google Drive file content content securely."""
        try:
            from src.tools.workspace.manager import GoogleWorkspaceManager
            mgr = GoogleWorkspaceManager()
            svc = mgr.get_drive_service()
            if not svc:
                return {"success": False, "error": "Service unavailable"}

            # Check specific exports
            if mime_type == 'application/vnd.google-apps.document':
                content = svc.files().export(fileId=file_id, mimeType='text/plain').execute()
                return {"success": True, "content": content.decode('utf-8')}
            elif mime_type == 'application/vnd.google-apps.spreadsheet':
                content = svc.files().export(fileId=file_id, mimeType='text/csv').execute()
                return {"success": True, "content": content.decode('utf-8')}
            
            # Regular download fallback
            try:
                content = svc.files().get_media(fileId=file_id).execute()
                return {"success": True, "content": content.decode('utf-8', errors='ignore')}
            except Exception:
                meta = svc.files().get(fileId=file_id, fields="name,webViewLink").execute()
                return {
                    "success": True, 
                    "content": f"[ Link: {meta.get('name')} ]\n{meta.get('webViewLink')}\n(Binary or large file. Content not loaded.)"
                }
        except Exception as e:
            logger.error(f"Drive fetch failed: {e}")
            return {"success": False, "error": str(e)}

    def check_google_auth(self) -> Dict[str, Any]:
        """Check authentication status securely."""
        try:
            from src.tools.workspace.manager import GoogleWorkspaceManager
            mgr = GoogleWorkspaceManager()
            is_authed, _ = mgr.authenticate()
            return {"success": True, "authenticated": is_authed}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_google_auth(self) -> Dict[str, Any]:
        """Launch local browser-based interactive OAuth flow."""
        try:
            from src.tools.workspace.manager import GoogleWorkspaceManager
            mgr = GoogleWorkspaceManager()
            success = mgr.run_local_auth_flow()
            return {"success": success}
        except Exception as e:
            return {"success": False, "error": str(e)}
