"""
Google Workspace Gmail Tools
"""

import base64
from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.workspace.manager import GoogleWorkspaceManager, parse_email_address

logger = setup_logger("GoogleWorkspace")

def handle_gmail_error(e: Exception, action: str) -> Dict[str, Any]:
    """Uniform error handler for Gmail tools to return actionable feedback to the agent."""
    if isinstance(e, PermissionError):
        logger.warning(f"Authentication required during {action}: {e}")
        return {
            "success": False,
            "error": (
                f"Authentication required: {str(e)}. "
                "Please call the 'refresh_google_auth' tool to re-authenticate with Google."
            )
        }
    elif isinstance(e, HttpError):
        status_code = e.resp.status if hasattr(e, 'resp') and e.resp else None
        reason = e.reason if hasattr(e, 'reason') else str(e)
        logger.error(f"Gmail API error during {action}: {e}", exc_info=True)
        if status_code == 404:
            return {
                "success": False,
                "error": (
                    f"⚠️ ERROR: The requested Gmail resource was not found (404) during {action}. "
                    "This usually means the message_id or thread_id is invalid or does not exist. "
                    "To fix this, call the 'search_gmail' tool first to find valid IDs."
                )
            }
        elif status_code == 403:
            return {
                "success": False,
                "error": (
                    f"⚠️ ERROR: Access forbidden (403) during {action}: {reason}. "
                    "This could mean insufficient API scopes or authentication issues. "
                    "Call the 'refresh_google_auth' tool to verify permissions."
                )
            }
        return {
            "success": False,
            "error": f"⚠️ ERROR: Gmail API error during {action}: {reason} (Status: {status_code})"
        }
    else:
        logger.error(f"System error during {action}: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"⚠️ ERROR: Failed during {action}: {str(e)}"
        }

def parse_message_body(part: Dict[str, Any]) -> str:
    """Recursively parse a Gmail message payload part to extract clean plain text."""
    import base64
    import re
    import html as html_lib

    mime_type = part.get('mimeType', '')
    body = part.get('body', {})
    data = body.get('data', '')
    
    # Base case: text/plain
    if mime_type == 'text/plain' and data:
        try:
            return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        except Exception:
            return ""
            
    # Recursive parts parsing
    parts = part.get('parts', [])
    text_parts = []
    html_parts = []
    
    for p in parts:
        p_mime = p.get('mimeType', '')
        if p_mime == 'text/plain':
            p_data = p.get('body', {}).get('data', '')
            if p_data:
                try:
                    text_parts.append(base64.urlsafe_b64decode(p_data).decode('utf-8', errors='ignore'))
                except Exception:
                    pass
        elif p_mime == 'text/html':
            p_data = p.get('body', {}).get('data', '')
            if p_data:
                try:
                    html_parts.append(base64.urlsafe_b64decode(p_data).decode('utf-8', errors='ignore'))
                except Exception:
                    pass
        elif p.get('parts'):
            decoded = parse_message_body(p)
            if decoded:
                text_parts.append(decoded)
                
    if text_parts:
        return "\n".join(text_parts)
    elif html_parts:
        # Fallback to HTML, strip tags
        html_text = "\n".join(html_parts)
        # remove scripts and styles
        html_text = re.sub(r'<script.*?>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
        html_text = re.sub(r'<style.*?>.*?</style>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
        # replace block elements with newlines
        html_text = re.sub(r'<br\s*/?>', '\n', html_text, flags=re.IGNORECASE)
        html_text = re.sub(r'</?(p|div|tr|h[1-6]).*?>', '\n', html_text, flags=re.IGNORECASE)
        # remove all other tags
        text = re.sub(r'<.*?>', '', html_text, flags=re.DOTALL)
        text = html_lib.unescape(text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        return text.strip()
        
    return ""


def _format_thread_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Lean per-message payload for read_gmail thread results."""
    payload = msg.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    from_raw = headers.get('from', 'Unknown')
    from_name, from_email = parse_email_address(from_raw)
    labels = msg.get('labelIds', [])
    return {
        "id": msg['id'],
        "from_name": from_name,
        "from_email": from_email,
        "to": headers.get('to', ''),
        "subject": headers.get('subject', 'No subject'),
        "date": headers.get('date', ''),
        "unread": "UNREAD" in labels,
        "body": parse_message_body(payload),
        "snippet": msg.get('snippet', ''),
    }


def _format_email_search_result(emails: List[Dict[str, Any]], hint: str) -> str:
    """Render search hits into the `result` string the UI/eval wire actually carries.

    Sibling `emails` is kept for structured consumers, but ``ui_state_syncer`` and
    the live eval harness only forward ``result`` on tool_end — a count-only summary
    made ``response_grounded`` mark real, model-visible subjects as hallucinations.
    """
    n = len(emails)
    if n == 0:
        return "No emails found."
    lines = [f"Found {n} emails:"]
    for i, em in enumerate(emails, 1):
        unread = " unread" if em.get("unread") else ""
        subj = em.get("subject") or "No subject"
        frm = em.get("from_name") or em.get("from_email") or "Unknown"
        date = em.get("date") or ""
        snippet = (em.get("snippet") or "").replace("\n", " ").strip()
        mid = em.get("id") or ""
        tid = em.get("thread_id") or ""
        lines.append(
            f"{i}. [{mid}]{unread} from={frm} subject={subj!r} date={date} "
            f"thread_id={tid} snippet={snippet!r}"
        )
    if hint:
        lines.append(hint)
    return "\n".join(lines)


class SearchGmailTool(BaseTool):
    """Tool to search and list recent emails from Gmail."""
    
    name = "search_gmail"
    description = (
        "READ: Search/list recent Gmail emails by sender, subject, or unread status. "
        "Returns metadata + longer snippets (not full bodies). For full message/thread "
        "contents, call read_gmail with id or thread_id from a result."
    )
    category = "workspace"

    # Soft cap on API snippet length (Gmail snippets are usually ≤~200 chars).
    SNIPPET_MAX = 300
    READ_HINT = (
        "To read full message/thread contents, call read_gmail with "
        "message_id=<id> or thread_id=<thread_id> from a result."
    )
    
    def format_action(self, params: Dict[str, Any]) -> str:
        query = params.get("query")
        if query:
            return f"Searching Gmail for '{query}'"
        return "Searching Gmail inbox"

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
                    "query": {
                        "type": "string",
                        "description": "Optional keywords to search for. Example: 'from:sam' or 'subject:scholarship'."
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "If true, only return unread emails. This is more robust than using 'is:unread' in a query."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default: 10, max: 25)"
                    }
                },
                "required": []
            }
        }
    
    # Hard ceiling on how many emails a single pull can return.
    MAX_LIMIT = 25

    async def execute(self, query: str = None, unread_only: bool = False, limit: int = 10, **kwargs) -> Dict[str, Any]:
        try:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = 10
            limit = max(1, min(limit, self.MAX_LIMIT))

            service = self._manager.get_gmail_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Gmail."
                }
            
            # Build robust search query
            search_parts = []
            if unread_only:
                # Use global unread but exclude trash/spam/drafts to match mobile 'All Inboxes'
                search_parts.append("is:unread -label:trash -label:spam -label:draft")
            
            if query:
                search_parts.append(query)
                
            # Final query: Default to inbox if nothing else specified
            # If unread_only is False and no query, we stick to in:inbox for sanity
            search_query = " ".join(search_parts) if search_parts else "in:inbox"
            
            logger.info(f"Gmail search: '{search_query}' (limit: {limit})")
            
            # Get message list
            results = service.users().messages().list(
                userId='me',
                q=search_query,
                maxResults=limit
            ).execute()
            
            messages = results.get('messages', [])
            
            if not messages:
                return {
                    "success": True,
                    "result": f"No emails found matching '{search_query}'",
                    "emails": []
                }
            
            # Fetch details for each message
            formatted_emails = []
            for msg in messages:
                msg_data = service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='metadata',
                    metadataHeaders=['From', 'Subject', 'Date']
                ).execute()
                
                headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
                
                # Parse the From header to extract display name and email
                from_raw = headers.get('From', 'Unknown')
                from_name, from_email = parse_email_address(from_raw)

                # Lean payload: keep only what the agent reasons over.
                labels = msg_data.get('labelIds', [])
                snippet = (msg_data.get('snippet') or '')[:self.SNIPPET_MAX]
                formatted_emails.append({
                    "id": msg['id'],
                    "thread_id": msg_data.get('threadId'),
                    "from_name": from_name,
                    "from_email": from_email,
                    "subject": headers.get('Subject', 'No subject'),
                    "date": headers.get('Date', ''),
                    "snippet": snippet,
                    "unread": "UNREAD" in labels,
                })
            
            return {
                "success": True,
                # `result` must carry the list text: UI tool_end + eval judges only
                # broadcast/score `result`, not sibling keys like `emails` (church_2h
                # false "hallucination" cluster 2026-07-26).
                "result": _format_email_search_result(formatted_emails, self.READ_HINT),
                "hint": self.READ_HINT,
                "emails": formatted_emails
            }
            
        except Exception as e:
            return handle_gmail_error(e, "searching emails")


class ReadGmailTool(BaseTool):
    """Tool to read an entire Gmail thread by message_id or thread_id."""
    
    name = "read_gmail"
    description = (
        "READ: Retrieve the full conversation thread for a Gmail message or thread. "
        "Pass message_id and/or thread_id (at least one). Always returns every message "
        "in the thread, not just a single body. Call search_gmail first if you need ids."
    )
    category = "workspace"
    
    def format_action(self, params: Dict[str, Any]) -> str:
        thread_id = params.get("thread_id") or ""
        msg_id = params.get("message_id") or ""
        if thread_id:
            return f"Reading Gmail thread {thread_id}"
        return f"Reading Gmail message {msg_id}"

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
                    "message_id": {
                        "type": "string",
                        "description": "A Gmail message ID. Resolves to its thread and returns the full conversation."
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "A Gmail thread ID. Returns every message in that thread."
                    }
                },
                "required": []
            }
        }

    async def execute(
        self,
        message_id: str = None,
        thread_id: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            message_id = message_id or kwargs.get("id")
            if not thread_id and not message_id:
                return {
                    "success": False,
                    "error": "You must provide either a message_id or a thread_id."
                }

            service = self._manager.get_gmail_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Gmail."
                }

            if not thread_id and message_id:
                logger.info(f"Resolving thread_id for message_id: {message_id}")
                try:
                    msg_meta = service.users().messages().get(
                        userId='me',
                        id=message_id,
                        format='metadata',
                        metadataHeaders=['threadId']
                    ).execute()
                    thread_id = msg_meta.get('threadId')
                except Exception as meta_err:
                    logger.warning(f"Failed to fetch metadata for {message_id}: {meta_err}")
                    thread_id = None

                if not thread_id:
                    # Fallback to single message retrieval if thread ID could not be resolved
                    msg_data = service.users().messages().get(
                        userId='me',
                        id=message_id,
                        format='full'
                    ).execute()
                    email = _format_thread_message(msg_data)
                    email["thread_id"] = None
                    return {
                        "success": True,
                        "email": email
                    }

            logger.info(f"Reading Gmail thread: {thread_id}")
            thread = service.users().threads().get(userId='me', id=thread_id).execute()
            messages = thread.get('messages', [])

            if not messages:
                return {
                    "success": False,
                    "error": f"No messages found in thread {thread_id}"
                }

            formatted_messages = [_format_thread_message(msg) for msg in messages]

            # Single-message threads: keep the compact 'email' shape for callers.
            if len(formatted_messages) == 1:
                return {
                    "success": True,
                    "email": formatted_messages[0],
                    "thread_id": thread_id,
                }

            return {
                "success": True,
                "result": f"Retrieved full conversation thread with {len(formatted_messages)} messages.",
                "thread_id": thread_id,
                "messages": formatted_messages
            }
        except Exception as e:
            return handle_gmail_error(e, "reading email")


class DraftGmailReplyTool(BaseTool):
    """Tool to create draft email replies."""
    
    name = "draft_gmail_reply"
    description = "WRITE: Create a draft reply to an email thread. The draft can be reviewed before sending."
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
                    "thread_id": {
                        "type": "string",
                        "description": "Thread ID of the email to reply to"
                    },
                    "body": {
                        "type": "string",
                        "description": "Body text of the reply"
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of absolute file paths to attach (e.g., ['/path/to/file.pdf'])"
                    }
                },
                "required": ["thread_id", "body"]
            }
        }
    
    async def execute(self, thread_id: str, body: str, attachments: List[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            service = self._manager.get_gmail_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Gmail."
                }
            
            # Get the original thread to find reply-to info
            thread = service.users().threads().get(userId='me', id=thread_id).execute()
            messages = thread.get('messages', [])
            
            if not messages:
                return {
                    "success": False,
                    "error": f"Thread not found: {thread_id}"
                }
            
            # Get the last message in the thread
            last_message = messages[-1]
            headers = {h['name']: h['value'] for h in last_message.get('payload', {}).get('headers', [])}
            
            # Build reply - parse email address from From header
            from_raw = headers.get('From', '')
            _, reply_to_email = parse_email_address(from_raw)
            reply_to = reply_to_email if reply_to_email else from_raw
            subject = headers.get('Subject', '')
            if not subject.lower().startswith('re:'):
                subject = f"Re: {subject}"
            message_id = headers.get('Message-ID', '')
            
            # Create MIME message using helper
            message = self._manager.create_message_with_attachments(reply_to, subject, body, attachments)
            if message_id:
                message['In-Reply-To'] = message_id
                message['References'] = message_id
            
            # Encode message
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Create draft
            draft = service.users().drafts().create(
                userId='me',
                body={
                    'message': {
                        'raw': raw,
                        'threadId': thread_id
                    }
                }
            ).execute()
            
            logger.info(f"Created draft reply: {draft['id']}")
            
            return {
                "success": True,
                "result": f"Created draft reply to '{subject}' - ready for review in Gmail",
                "draft_id": draft['id']
            }
            
        except Exception as e:
            return handle_gmail_error(e, "creating draft reply")


class SendEmailTool(BaseTool):
    """Tool to send emails (requires confirmation)."""
    
    name = "send_email"
    description = "WRITE: Send an email via Gmail. Only use when explicitly asked to 'send' or 'email' someone. Requires confirmation."
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
                    "to": {
                        "type": "string",
                        "description": "Recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line"
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text"
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of absolute file paths to attach (e.g., ['/path/to/file.pdf'])"
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set to true to confirm sending (required for this action)"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        }
    
    async def execute(self, to: str, subject: str, body: str, attachments: List[str] = None, confirmed: bool = False, **kwargs) -> Dict[str, Any]:
        try:
            # SAFETY: Verify recipient email exists in user's known contacts/email history
            # This prevents sending to guessed/hallucinated addresses
            verification = self._manager.verify_email_address(to)
            if not verification.get('verified'):
                logger.warning(f"BLOCKED: Attempted to send email to unverified address: {to}")
                return {
                    "success": False,
                    "error": f"BLOCKED: Email address '{to}' is not verified. "
                             f"I can only send to addresses found in your Contacts, Other Contacts, "
                             f"or your sent/received email history. "
                             f"Please search your contacts or ask the user for the correct address.",
                    "_clarification_mode": True  # Signal to ask user for correct address
                }
            
            logger.info(f"Email verified via: {verification.get('source')}")
            
            # If not confirmed, return confirmation request
            if not confirmed:
                return {
                    "success": False,
                    "needs_confirmation": True,
                    "action": "send_email",
                    "details": {"to": to, "subject": subject, "verified_via": verification.get('source')},
                    "message": f"Send email to {to} (verified via {verification.get('source')}) with subject '{subject}'?"
                }
            
            service = self._manager.get_gmail_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Gmail."
                }
            
            # Create MIME message using helper
            message = self._manager.create_message_with_attachments(to, subject, body, attachments)
            
            # Encode message
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Send message
            sent_message = service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()
            
            logger.info(f"Sent email: {sent_message['id']}")
            
            return {
                "success": True,
                "result": f"Email sent to {to}",
                "message_id": sent_message['id']
            }
            
        except Exception as e:
            return handle_gmail_error(e, "sending email")


class CreateGmailDraftTool(BaseTool):
    """Tool to create a NEW draft email (not a reply)."""
    
    name = "create_gmail_draft"
    description = "WRITE: Create a new draft email. Use this when starting a NEW conversation (not replying)."
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
                    "to": {
                        "type": "string",
                        "description": "Recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject"
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text"
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of absolute file paths to attach"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        }
    
    async def execute(self, to: str, subject: str, body: str, attachments: List[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            # OPTIONAL SAFETY: Verify recipient (warn but allow drafts, as they are safe)
            verification = self._manager.verify_email_address(to)
            if not verification.get('verified'):
                 logger.warning(f"Drafting email to unverified address {to}. This is allowed for drafts.")
            
            service = self._manager.get_gmail_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Gmail."
                }
            
            # Create MIME message using helper
            message = self._manager.create_message_with_attachments(to, subject, body, attachments)
            
            # Encode message
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Create draft
            draft = service.users().drafts().create(
                userId='me',
                body={
                    'message': {
                        'raw': raw
                    }
                }
            ).execute()
            
            logger.info(f"Created new draft: {draft['id']}")
            
            return {
                "success": True,
                "result": f"Created new draft to '{to}' with subject '{subject}'. You can review/send it in Gmail.",
                "draft_id": draft['id']
            }
            
        except Exception as e:
            return handle_gmail_error(e, "creating new email draft")
