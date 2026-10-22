"""
Google Workspace Gmail History Tool
"""

from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.workspace.manager import GoogleWorkspaceManager, parse_email_address

logger = setup_logger("GmailHistoryTool")

class GetGmailHistoryTool(BaseTool):
    """Tool to fetch specific email changes using a historyId."""
    
    name = "get_gmail_history"
    description = (
        "Get specific email changes starting from a historyId. "
        "Use this when a Pub/Sub notification provides a history_id to find exactly which email was added."
    )
    category = "workspace"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_manager: Optional[GoogleWorkspaceManager] = None):
        super().__init__(config)
        self._manager = workspace_manager or GoogleWorkspaceManager()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "history_id": {
                        "type": "string",
                        "description": "The historyId from the Gmail Pub/Sub notification."
                    }
                },
                "required": ["history_id"]
            }
        }
    
    async def execute(self, history_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Execute history lookup.
        
        Args:
            history_id: The historyId from the notification. 
                       If not provided, uses the last stored ID.
        """
        try:
            import asyncio
            service = self._manager.get_gmail_service()
            if not service:
                return {"success": False, "error": "Failed to connect to Gmail."}
            
            # Identify the start point
            last_id = await asyncio.to_thread(self._manager.get_last_history_id)
            
            # Logic: If we have a history_id from notification, and it's the SAME as last_id, 
            # nothing to do. If it's NEWER, we use last_id as the start point.
            start_id = last_id
            
            if not start_id:
                if history_id:
                    # Fallback: start from one before the notification
                    # This is better than nothing if state was lost
                    start_id = str(int(history_id) - 1)
                else:
                    return {"success": False, "error": "No history point provided and no state found."}

            logger.info(f"[ GMAIL ] Processing history from {start_id} to {history_id or 'latest'}")
            
            # 1. List history records
            history_records = await asyncio.to_thread(self._manager.list_history, start_id)
            if not history_records:
                return {
                    "success": True,
                    "result": f"No history records found for historyId {history_id}. The message might have been already processed or the ID is too old.",
                    "emails": []
                }
            
            # 2. Extract message IDs of ADDED messages
            new_message_ids = []
            for record in history_records:
                messages_added = record.get('messagesAdded', [])
                for entry in messages_added:
                    msg_id = entry.get('message', {}).get('id')
                    if msg_id and msg_id not in new_message_ids:
                        new_message_ids.append(msg_id)
            
            if not new_message_ids:
                return {
                    "success": True,
                    "result": f"History was found for {history_id}, but no new messages were added (it might have been a label change).",
                    "emails": []
                }
            
            # 3. Fetch details for each new message
            formatted_emails = []
            for msg_id in new_message_ids:
                try:
                    msg_data = service.users().messages().get(
                        userId='me',
                        id=msg_id,
                        format='metadata',
                        metadataHeaders=['From', 'Subject', 'Date']
                    ).execute()
                    
                    headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
                    from_raw = headers.get('From', 'Unknown')
                    from_name, from_email = parse_email_address(from_raw)
                    
                    formatted_emails.append({
                        "id": msg_id,
                        "thread_id": msg_data.get('threadId'),
                        "from": from_raw,
                        "from_name": from_name,
                        "from_email": from_email,
                        "subject": headers.get('Subject', 'No subject'),
                        "date": headers.get('Date', ''),
                        "snippet": msg_data.get('snippet', '')[:200], # Longer snippet for history lookup
                        "labels": msg_data.get('labelIds', [])
                    })
                except HttpError as e:
                    if e.resp.status == 404:
                        logger.info(f"[ GMAIL ] Message {msg_id} not found (likely deleted or draft discarded). Skipping.")
                    else:
                        logger.warning(f"Failed to fetch details for message {msg_id}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to fetch details for message {msg_id}: {e}")
            
            if formatted_emails and history_id:
                # 4. Successful discovery - update the persistent cursor
                await asyncio.to_thread(self._manager.save_last_history_id, history_id)
                logger.info(f"[ GMAIL ] Advanced history cursor to {history_id}")

            return {
                "success": True,
                "result": f"Found {len(formatted_emails)} new message(s) in history event {history_id}",
                "emails": formatted_emails
            }
            
        except HttpError as e:
            logger.error(f"Gmail API error: {e}", exc_info=True)
            return {"success": False, "error": f"Gmail API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to get Gmail history: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
