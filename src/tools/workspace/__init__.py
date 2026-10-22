# Google Workspace Tools Package
# Re-exports all tools for backward compatibility

from src.tools.workspace.manager import GoogleWorkspaceManager, parse_email_address

# Gmail Tools
from src.tools.workspace.gmail import (
    SearchGmailTool,
    ReadGmailTool,
    DraftGmailReplyTool,
    SendEmailTool,
    CreateGmailDraftTool,
)
from src.tools.workspace.auth import (
    RefreshGoogleAuthTool,
)

# Contacts Tools
from src.tools.workspace.contacts import (
    SearchContactsTool,
)

# Google Drive & Docs Tools
from src.tools.workspace.drive import (
    GoogleDriveListFolderTool,
    GoogleDriveSearchTool,
    GoogleDriveReadFileTool,
    GoogleDriveCreateDocTool,
    GoogleDriveEditDocTool,
)

# Google Calendar Tools
from src.tools.workspace.calendar import (
    GetCalendarEventsTool,
    AddCalendarEventTool,
    UpdateCalendarEventTool,
    get_upcoming_schedule_context,
)

# Outlook Desktop Tools
from src.tools.workspace.outlook import (
    ReadOutlookEmailsTool,
    SearchOutlookEmailsTool,
)

__all__ = [
    'GoogleWorkspaceManager',
    'parse_email_address',
    # Gmail
    'SearchGmailTool',
    'ReadGmailTool',
    'DraftGmailReplyTool',
    'SendEmailTool',
    'CreateGmailDraftTool',
    'RefreshGoogleAuthTool',
    # Outlook
    'ReadOutlookEmailsTool',
    'SearchOutlookEmailsTool',
    # Contacts
    'SearchContactsTool',
    # Google Drive & Docs
    'GoogleDriveListFolderTool',
    'GoogleDriveSearchTool',
    'GoogleDriveReadFileTool',
    'GoogleDriveCreateDocTool',
    'GoogleDriveEditDocTool',
    # Google Calendar
    'GetCalendarEventsTool',
    'AddCalendarEventTool',
    'UpdateCalendarEventTool',
    'DeleteCalendarEventTool',
    'get_upcoming_schedule_context',
]
