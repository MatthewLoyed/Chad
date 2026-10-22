"""
Google Workspace integration commands.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from .base_command import Command


@dataclass
class ListDriveFilesCommand(Command):
    """Command to list or search Google Drive files."""

    query: Optional[str] = None
    limit: int = 50

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: list drive files."""
        return facade.list_drive_files(query=self.query, limit=self.limit)

    def __str__(self) -> str:
        return f"ListDriveFilesCommand(query='{self.query}', limit={self.limit})"


@dataclass
class GetDriveFileContentCommand(Command):
    """Command to retrieve content from a specific Google Drive file."""

    file_id: str
    mime_type: str

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate required parameters."""
        if not self.file_id:
            return False, "File ID is required"
        if not self.mime_type:
            return False, "MIME type is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: get file content."""
        return facade.get_drive_file_content(file_id=self.file_id, mime_type=self.mime_type)

    def __str__(self) -> str:
        return f"GetDriveFileContentCommand(file_id='{self.file_id}')"


@dataclass
class CheckGoogleAuthCommand(Command):
    """Command to check current Google OAuth session status."""

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: check authentication."""
        return facade.check_google_auth()

    def __str__(self) -> str:
        return "CheckGoogleAuthCommand()"


@dataclass
class StartGoogleAuthCommand(Command):
    """Command to launch interactive local OAuth browser flow."""

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: launch OAuth flow."""
        return facade.start_google_auth()

    def __str__(self) -> str:
        return "StartGoogleAuthCommand()"
