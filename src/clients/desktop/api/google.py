from src.application.commands import (
    ListDriveFilesCommand,
    GetDriveFileContentCommand,
    CheckGoogleAuthCommand,
    StartGoogleAuthCommand
)


class GoogleAPIMixin:
    """Mixin for Google-related WebView API methods (Workspace, Auth, Drive)."""

    def list_drive_files(self, query=None, limit=50):
        """
        List or search files in Google Drive.
        Returns a list of file objects: {id, name, mimeType, modifiedTime, size}
        """
        return self._execute_command(
            ListDriveFilesCommand(query=query, limit=limit),
            timeout=15.0,
            default_fallback={"success": False, "error": "Command system not available"}
        )

    def get_drive_file_content(self, file_id, mime_type):
        """
        Download/Export content of a Google Drive file.
        """
        return self._execute_command(
            GetDriveFileContentCommand(file_id=file_id, mime_type=mime_type),
            timeout=20.0,
            default_fallback={"success": False, "error": "Command system not available"}
        )

    def check_google_auth(self):
        """Check if Google Workspace is authenticated."""
        return self._execute_command(
            CheckGoogleAuthCommand(),
            timeout=10.0,
            default_fallback={"success": False, "authenticated": False}
        )
    
    def start_google_auth(self):
        """Trigger the interactive OAuth browser flow."""
        return self._execute_command(
            StartGoogleAuthCommand(),
            timeout=120.0,
            default_fallback={"success": False, "error": "Command system unavailable"}
        )
