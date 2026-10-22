"""
Google Workspace Manager - OAuth2 Authentication and Service Access
Singleton manager for Google API authentication and service instantiation.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import mimetypes

from src.shared.logger import setup_logger

logger = setup_logger("GoogleWorkspace")


# Google API imports - will be verified at runtime
# try:
#     from google.oauth2.credentials import Credentials
#     from google_auth_oauthlib.flow import InstalledAppFlow
#     from google.auth.transport.requests import Request
#     from googleapiclient.discovery import build
#     from googleapiclient.errors import HttpError
#     GOOGLE_APIS_AVAILABLE = True
# except ImportError as e:
#     GOOGLE_APIS_AVAILABLE = False
#     logger.error(f"Google API libraries not installed: {e}")
GOOGLE_APIS_AVAILABLE = True # Assumed true for lazy loading check, or handled in methods



# OAuth scopes required for Calendar, Gmail, Docs, Drive, and Contacts
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/contacts.readonly',       # For People API contact search
    'https://www.googleapis.com/auth/contacts.other.readonly', # For "Other Contacts" search
]


def parse_email_address(header_value: str) -> tuple[str, str]:
    """
    Parse an email header to extract display name and email address.
    
    Examples:
        "Sam Smith <sam@example.com>" -> ("Sam Smith", "sam@example.com")
        "sam@example.com" -> ("", "sam@example.com")
    
    Returns:
        Tuple of (display_name, email_address)
    """
    import re
    # Match "Name <email>" format
    match = re.match(r'^"?([^"<]*)"?\s*<([^>]+)>$', header_value.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    # Plain email address
    email_match = re.match(r'^[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}$', header_value.strip())
    if email_match:
        return "", header_value.strip()
    return header_value, ""


class GoogleWorkspaceManager:
    """
    Singleton manager for Google Workspace authentication and service access.
    Handles OAuth2 flow and token refresh.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._credentials: Optional[Any] = None
        self._calendar_service = None
        self._gmail_service = None
        self._docs_service = None
        self._drive_service = None
        self._people_service = None  # For contact search
        
        from src.shared.paths import get_project_root
        self._project_root = get_project_root()
        self._credentials_path = self._project_root / "config" / "credentials.json"
        self._token_path = self._project_root / "config" / "token.json"
        
        # Professional State Persistence
        try:
            from src.infrastructure.repositories.state_repository import StateRepository
            self._state = StateRepository()
        except ImportError:
            logger.warning("StateRepository not available. Gmail history tracking will be ephemeral.")
            self._state = None
            
        self._initialized = True

    def get_last_history_id(self) -> Optional[str]:
        """Retrieve the last processed Gmail history ID from Supabase."""
        if not self._state:
            return None
        return self._state.get_value("gmail_last_history_id")

    def save_last_history_id(self, history_id: str) -> bool:
        """Persist the last processed Gmail history ID to Supabase."""
        if not self._state:
            return False
        logger.debug(f"[ GMAIL ] Persisting history cursor: {history_id}")
        return self._state.set_value("gmail_last_history_id", history_id)
    
    def _bootstrap_from_env(self):
        """
        Write credentials/token files from environment variables if the files
        don't exist on disk. Used for cloud deployments (Render) where there
        is no persistent filesystem.

        Expected env vars (base64-encoded file contents):
            GOOGLE_CREDENTIALS_JSON  – contents of credentials.json
            GOOGLE_TOKEN_JSON        – contents of token.json
        """
        import base64
        import json

        for env_var, dest_path in [
            ("GOOGLE_CREDENTIALS_JSON", self._credentials_path),
            ("GOOGLE_TOKEN_JSON", self._token_path),
        ]:
            # Priority: If file exists on disk, assume it's fresh (especially for local dev)
            if dest_path.exists():
                continue

            value = os.getenv(env_var)
            if value:
                try:
                    # Decrypt/Decode value
                    value = value.strip()
                    # Remove prefix if accidentally included in copy-paste
                    if value.startswith(f"{env_var}="):
                        value = value[len(env_var)+1:].strip()

                    # Fix padding if missing
                    padding_needed = len(value) % 4
                    if padding_needed:
                        value += "=" * (4 - padding_needed)
                    
                    decoded = base64.b64decode(value).decode("utf-8")
                    
                    # Verify it's actually JSON before writing
                    json.loads(decoded)
                    
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_text(decoded)
                    logger.info(f"Wrote {dest_path.name} from env var {env_var}")
                except Exception as e:
                    logger.error(f"Failed to write {dest_path.name} from {env_var}: {e}")

        # Sync Node.js token format if token.json exists on disk
        if self._token_path.exists():
            self._sync_node_token()

    def _sync_node_token(self):
        """Translate Python-format token.json to Node.js-format token_node.json for Google Drive MCP."""
        import json
        from datetime import datetime
        try:
            if not self._token_path.exists():
                return
            
            with open(self._token_path, 'r') as f:
                py_token = json.load(f)
                
            expiry_str = py_token.get('expiry')
            if not expiry_str:
                return
                
            if expiry_str.endswith('Z'):
                expiry_str = expiry_str[:-1] + '+00:00'
            expiry_dt = datetime.fromisoformat(expiry_str)
            expiry_ms = int(expiry_dt.timestamp() * 1000)
            
            node_token = {
                "access_token": py_token.get('token'),
                "refresh_token": py_token.get('refresh_token'),
                "scope": " ".join(py_token.get('scopes', [])),
                "token_type": "Bearer",
                "expiry_date": expiry_ms
            }
            
            node_token_path = self._token_path.parent / "token_node.json"
            with open(node_token_path, 'w') as f:
                json.dump(node_token, f, indent=2)
            logger.info(f"[ WORKSPACE ] Synchronized Node.js token to {node_token_path.name}")
        except Exception as e:
            logger.error(f"[ WORKSPACE ] Failed to synchronize Node.js token: {e}")

    def authenticate(self) -> tuple[bool, str]:
        """
        Authenticate with Google APIs using OAuth2.
        
        Returns:
            Tuple of (Success: bool, ErrorMessage: str)
        """
        if not GOOGLE_APIS_AVAILABLE:
            msg = "Google API libraries not available."
            logger.error(msg)
            return False, msg

        # Ensure we have credentials from env if applicable
        self._bootstrap_from_env()

        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            import base64

            creds = None
            if self._token_path.exists():
                creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
                logger.info("Loaded credentials from token.json")

            # Validate and refresh if necessary
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired credentials...")
                    creds.refresh(Request())

                    token_json = creds.to_json()

                    # The refreshed token is deliberately NOT logged. This used to emit the
                    # whole thing base64-encoded as "NEW_TOKEN_JSON: …" for copy-pasting into
                    # Render's env vars — which meant every refresh wrote a live refresh token
                    # into a plaintext log file that persists indefinitely (18 such files were
                    # found on this machine). Base64 is not encryption; anyone who could read
                    # the logs could take over the Google account's granted scopes. Use
                    # `run_local_auth_flow`, which prints the blob to an interactive console
                    # on purpose, when a cloud deploy genuinely needs it.
                    logger.info("Google credentials refreshed successfully.")

                    # Save updated token locally.
                    #
                    # Truncating write, NOT write-temp-then-rename, and that matters: the dev
                    # worktree hardlinks this path to the main checkout's copy so one
                    # re-authentication serves both (see scripts/prod/setup_dev_worktree.ps1).
                    # A rename would create a new inode and silently break that link, putting
                    # the two checkouts back on divergent refresh tokens. If this ever needs to
                    # become atomic, switch both checkouts to a shared path via env var first
                    # (the CHAD_LOCAL_DB_PATH pattern).
                    with open(self._token_path, 'w') as token_file:
                        token_file.write(token_json)
                    
                    # Sync to Node.js format
                    self._sync_node_token()
                        
                except Exception as e:
                    logger.error(f"Token refresh failed: {e}")
                    creds = None

            if not creds or not creds.valid:
                msg = "GOOGLE_AUTH_REQUIRED: Credentials are missing, expired, or revoked."
                logger.critical(msg)
                return False, msg

            self._credentials = creds
            return True, ""

        except Exception as e:
            msg = f"Authentication failed: {str(e)}"
            logger.error(msg)
            return False, msg

    def run_local_auth_flow(self) -> bool:
        """
        Runs the interactive OAuth browser flow.
        USE THIS ONLY LOCALLY. Not supported on cloud servers.
        
        Returns:
            True if authentication successful and token saved.
        """
        if not GOOGLE_APIS_AVAILABLE:
            logger.error("Google API libraries not available.")
            return False

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            import base64

            if not self._credentials_path.exists():
                logger.error(f"credentials.json not found at {self._credentials_path}")
                return False

            logger.info("Starting local OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._credentials_path),
                SCOPES
            )
            creds = flow.run_local_server(port=0)
            
            # Save credentials locally
            token_json = creds.to_json()
            with open(self._token_path, 'w') as token:
                token.write(token_json)
            
            # Sync to Node.js format
            self._sync_node_token()
            
            # Print base64 version for cloud setup
            b64_token = base64.b64encode(token_json.encode('utf-8')).decode('utf-8')
            print(f"\n--- SUCCESS ---\nTOKEN SAVED TO: {self._token_path}\n")
            # This path also runs through refresh_google_auth inside the server.
            # Never print reusable OAuth credentials into tool/server logs.
            
            self._credentials = creds
            # Cached API clients retain the old credentials object after reauth.
            self._calendar_service = self._gmail_service = self._docs_service = None
            self._drive_service = self._people_service = None
            
            # --- UPDATE .ENV FOR PERISTENCE ---
            try:
                env_path = self._project_root / ".env"
                if env_path.exists():
                    env_content = env_path.read_text("utf-8")
                    lines = env_content.splitlines()
                    updated_lines = []
                    found = False
                    
                    for line in lines:
                        if line.startswith("GOOGLE_TOKEN_JSON="):
                            updated_lines.append(f"GOOGLE_TOKEN_JSON={b64_token}")
                            found = True
                        else:
                            updated_lines.append(line)
                    
                    if not found:
                        updated_lines.append(f"GOOGLE_TOKEN_JSON={b64_token}")
                    
                    env_path.write_text("\n".join(updated_lines), "utf-8")
                    logger.info("Updated GOOGLE_TOKEN_JSON in .env")
                else:
                    logger.warning(".env file not found, skipping GOOGLE_TOKEN_JSON update.")
            except Exception as e:
                logger.error(f"Failed to update .env with new token: {e}")
            # ---------------------------------

            return True

        except Exception as e:
            logger.error(f"Local OAuth flow failed: {e}")
            return False
    
    def get_calendar_service(self):
        """Get Google Calendar API service."""
        if not self._credentials:
            success, error = self.authenticate()
            if not success:
                raise PermissionError(f"Calendar access denied: {error}")
        
        if not self._calendar_service:
            try:
                from googleapiclient.discovery import build
                self._calendar_service = build('calendar', 'v3', credentials=self._credentials)
                logger.info("Calendar service initialized")
            except Exception as e:
                logger.error(f"Failed to build Calendar service: {e}")
                return None
        
        return self._calendar_service
    
    def get_gmail_service(self):
        """Get Gmail API service."""
        if not self._credentials:
            success, error = self.authenticate()
            if not success:
                # Raise an error that tools can catch to recommend refresh_google_auth
                raise PermissionError(f"Gmail access denied: {error}")
        
        if not self._gmail_service:
            try:
                from googleapiclient.discovery import build
                self._gmail_service = build('gmail', 'v1', credentials=self._credentials)
                logger.info("Gmail service initialized")
            except Exception as e:
                logger.error(f"Failed to build Gmail service: {e}")
                return None
        
        return self._gmail_service
    
    def get_docs_service(self):
        """Get Google Docs API service."""
        if not self._credentials:
            success, error = self.authenticate()
            if not success:
                raise PermissionError(f"Docs access denied: {error}")
        
        if not self._docs_service:
            try:
                from googleapiclient.discovery import build
                self._docs_service = build('docs', 'v1', credentials=self._credentials)
                logger.info("Docs service initialized")
            except Exception as e:
                logger.error(f"Failed to build Docs service: {e}")
                return None
        
        return self._docs_service
    
    def get_drive_service(self):
        """Get Google Drive API service."""
        if not self._credentials:
            success, error = self.authenticate()
            if not success:
                raise PermissionError(f"Drive access denied: {error}")
        
        if not self._drive_service:
            try:
                from googleapiclient.discovery import build
                self._drive_service = build('drive', 'v3', credentials=self._credentials)
                logger.info("Drive service initialized")
            except Exception as e:
                logger.error(f"Failed to build Drive service: {e}")
                return None
        
        return self._drive_service
    
    def get_people_service(self):
        """Get Google People API service for contact search."""
        if not self._credentials:
            success, error = self.authenticate()
            if not success:
                raise PermissionError(f"People access denied: {error}")
        
        if not self._people_service:
            try:
                from googleapiclient.discovery import build
                self._people_service = build('people', 'v1', credentials=self._credentials)
                logger.info("People service initialized")
            except Exception as e:
                logger.error(f"Failed to build People service: {e}")
                return None
        
        return self._people_service
    
    def watch_gmail(self, topic_name: str) -> Dict[str, Any]:
        """
        Start watching for Gmail changes (Push Notifications via Pub/Sub).
        
        Args:
            topic_name: Full topic path (projects/ID/topics/NAME)
            
        Returns:
            Dict with 'historyId' and 'expiration' (Unix ms)
        """
        service = self.get_gmail_service()
        if not service:
            logger.error("Gmail service not available for watch")
            return {}
            
        try:
            request_body = {
                'topicName': topic_name,
                'labelIds': ['INBOX']
            }
            result = service.users().watch(userId='me', body=request_body).execute()
            logger.info(f"[ GMAIL ] Watch established: {result}")
            return result
        except Exception as e:
            logger.error(f"[ GMAIL ] Failed to establish watch: {e}")
            return {}
    
    def list_history(self, start_history_id: str) -> List[Dict[str, Any]]:
        """
        List Gmail history starting from a specific historyId.
        
        Args:
            start_history_id: The historyId to start from. 
                             Usually came from a previous watch or push notification.
                             
        Returns:
            List of history record dictionaries.
        """
        service = self.get_gmail_service()
        if not service:
            logger.error("Gmail service not available for history list")
            return []
            
        try:
            logger.info(f"[ GMAIL ] Listing history from: {start_history_id}")
            results = service.users().history().list(
                userId='me',
                startHistoryId=start_history_id,
                historyTypes=['messageAdded'] # Focus on new messages
            ).execute()
            
            return results.get('history', [])
        except Exception as e:
            logger.error(f"[ GMAIL ] Failed to list history from {start_history_id}: {e}")
            return []

    def create_message_with_attachments(self, to: str, subject: str, body: str, attachment_paths: List[str] = None):
        """Create a MIME message with optional attachments."""
        if not attachment_paths:
            message = MIMEText(body)
            message['to'] = to
            message['subject'] = subject
            return message

        message = MIMEMultipart()
        message['to'] = to
        message['subject'] = subject
        message.attach(MIMEText(body, 'plain'))

        for path in attachment_paths:
            try:
                path_obj = Path(path)
                if not path_obj.exists():
                    logger.warning(f"Attachment not found: {path}")
                    continue
                
                # Check size limit (25MB)
                if path_obj.stat().st_size > 25 * 1024 * 1024:
                    logger.warning(f"Attachment too large (>25MB): {path}")
                    continue

                ctype, encoding = mimetypes.guess_type(path)
                if ctype is None or encoding is not None:
                    ctype = 'application/octet-stream'
                
                maintype, subtype = ctype.split('/', 1)
                
                with open(path, 'rb') as fp:
                    msg = MIMEBase(maintype, subtype)
                    msg.set_payload(fp.read())
                
                encoders.encode_base64(msg)
                msg.add_header('Content-Disposition', 'attachment', filename=path_obj.name)
                message.attach(msg)
                logger.info(f"Attached file: {path_obj.name}")
                
            except Exception as e:
                logger.error(f"Failed to attach {path}: {e}")
                
        return message
    
    def verify_email_address(self, email: str) -> Dict[str, Any]:
        """
        Verify an email address exists in user's known contacts or email history.
        
        Checks:
        0. User's own Gmail profile (for sending to self)
        1. Google Contacts (including "Other Contacts")
        2. Recently sent emails (last 100)
        3. Recently received emails (last 100)
        
        Returns:
            Dict with 'verified': bool and 'source': str if found
        """
        email_lower = email.lower().strip()
        
        # 0. Check if this is the user's own email (Gmail profile)
        try:
            gmail_service = self.get_gmail_service()
            if gmail_service:
                profile = gmail_service.users().getProfile(userId='me').execute()
                user_email = profile.get('emailAddress', '').lower()
                if user_email and user_email == email_lower:
                    return {'verified': True, 'source': 'Your Gmail account'}
        except Exception as e:
            logger.debug(f"Gmail profile check error (non-fatal): {e}")
        
        # 1. Check Google Contacts
        try:
            people_service = self.get_people_service()
            if people_service:
                # Search main contacts
                results = people_service.people().searchContacts(
                    query=email_lower,
                    readMask='emailAddresses,names',
                    sources=['READ_SOURCE_TYPE_CONTACT']
                ).execute()
                
                for person in results.get('results', []):
                    person_data = person.get('person', {})
                    emails = person_data.get('emailAddresses', [])
                    for e in emails:
                        if e.get('value', '').lower() == email_lower:
                            name = person_data.get('names', [{}])[0].get('displayName', 'Contact')
                            return {'verified': True, 'source': f'Google Contact: {name}'}
                
                # Search "Other Contacts" (people you've emailed but not added)
                other_results = people_service.otherContacts().search(
                    query=email_lower,
                    readMask='emailAddresses,names'
                ).execute()
                
                for person in other_results.get('results', []):
                    person_data = person.get('person', {})
                    emails = person_data.get('emailAddresses', [])
                    for e in emails:
                        if e.get('value', '').lower() == email_lower:
                            name = person_data.get('names', [{}])[0].get('displayName', 'Other Contact')
                            return {'verified': True, 'source': f'Other Contact: {name}'}
        except Exception as e:
            logger.debug(f"Contact search error (non-fatal): {e}")
        
        # 2. Check sent emails
        try:
            gmail_service = self.get_gmail_service()
            if gmail_service:
                # Search sent emails for this recipient
                sent_query = f"to:{email_lower}"
                sent_results = gmail_service.users().messages().list(
                    userId='me',
                    q=sent_query,
                    maxResults=10
                ).execute()
                
                if sent_results.get('messages'):
                    return {'verified': True, 'source': 'Previously sent email to this address'}
                
                # 3. Check received emails
                from_query = f"from:{email_lower}"
                from_results = gmail_service.users().messages().list(
                    userId='me',
                    q=from_query,
                    maxResults=10
                ).execute()
                
                if from_results.get('messages'):
                    return {'verified': True, 'source': 'Previously received email from this address'}
        except Exception as e:
            logger.debug(f"Gmail search error (non-fatal): {e}")
        
        # Not found in any source
        return {'verified': False, 'source': None}
