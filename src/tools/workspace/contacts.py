"""
Google Workspace Contacts Tools
"""

from typing import Dict, Any, Optional
from googleapiclient.errors import HttpError

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.tools.workspace.manager import GoogleWorkspaceManager

logger = setup_logger("GoogleWorkspace")

class SearchContactsTool(BaseTool):
    """Tool to search Google contacts for email addresses."""
    
    name = "search_contacts"
    description = "Search your Google contacts by name to find email addresses. Use this when you need to email someone but only know their name (e.g., 'find Sarah Spry's email')."
    category = "contacts"
    
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
                    "name": {
                        "type": "string",
                        "description": "Name to search for (e.g., 'Sarah', 'John Smith')"
                    }
                },
                "required": ["name"]
            }
        }
    
    async def execute(self, name: str, **kwargs) -> Dict[str, Any]:
        try:
            service = self._manager.get_people_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Google People API. You may need to re-authenticate to grant contacts access."
                }
            
            # 1. Search "My Contacts"
            results = service.people().searchContacts(
                query=name,
                readMask="names,emailAddresses",
                pageSize=10
            ).execute()
            
            contacts = results.get('results', [])
            formatted_contacts = []
            
            # Helper to process a person object
            def process_person(person_data):
                names = person_data.get('names', [{}])
                emails = person_data.get('emailAddresses', [])
                
                display_name = names[0].get('displayName', 'Unknown') if names else 'Unknown'
                email_list = [e.get('value') for e in emails if e.get('value')]
                
                if email_list:
                    return {
                        "name": display_name,
                        "emails": email_list,
                        "primary_email": email_list[0],
                        "source": "Contacts"
                    }
                return None

            for contact in contacts:
                p = process_person(contact.get('person', {}))
                if p: formatted_contacts.append(p)
            
            # 2. If few results, search "Other Contacts" (frequently contacted)
            if len(formatted_contacts) < 5:
                # logger.debug(f"[Contacts] Searching Other Contacts for '{name}'...")
                try:
                    other_results = service.otherContacts().search(
                        query=name,
                        readMask="names,emailAddresses"
                    ).execute()
                    
                    other_contacts = other_results.get('otherContacts', [])
                    
                    for oc in other_contacts:
                        # OtherContacts structure is flat, not wrapped in 'person'
                        p = process_person(oc)
                        if p:
                            p['source'] = 'Other Contacts'
                            # Avoid duplicates
                            if not any(existing['primary_email'] == p['primary_email'] for existing in formatted_contacts):
                                formatted_contacts.append(p)
                    
                except Exception as e:
                    logger.warning(f"Failed to search Other Contacts: {e}")
            
            if not formatted_contacts:
                return {
                    "success": True,
                    "result": f"No contacts found matching '{name}' in Contacts or Other Contacts.",
                    "contacts": []
                }
            
            # Build response summary
            summary = f"Found {len(formatted_contacts)} contact(s) matching '{name}':\n"
            for c in formatted_contacts:
                summary += f"  - {c['name']} ({c['source']}): {c['primary_email']}\n"
            
            return {
                "success": True,
                "result": summary,
                "contacts": formatted_contacts
            }
            
        except PermissionError as e:
            logger.warning(f"Authentication required: {e}")
            return {
                "success": False,
                "error": (
                    f"Authentication required: {str(e)}. "
                    "Please call the 'refresh_google_auth' tool to re-authenticate with Google."
                )
            }
        except HttpError as e:
            logger.error(f"People API error: {e}", exc_info=True)
            return {"success": False, "error": f"People API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to search contacts: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

