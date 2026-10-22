"""
Chat History Commands
Commands for managing persistent conversation history.
"""

import re
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from src.application.commands.base_command import Command
from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
from src.infrastructure.repositories.domain_repository import DomainRepository
from src.shared.config_manager import ConfigManager
from src.tools.os.workspace_tools import ProjectDiscovery


def _sanitize_project_name(name: str) -> str:
    """Reduce a user-supplied project name to a safe single folder-name component."""
    name = (name or "").strip()
    # Strip any path separators / parent-dir traversal — this must resolve to a
    # single new child of a coding root, never an arbitrary filesystem path.
    name = re.sub(r'[\\/]+', ' ', name).strip()
    name = name.replace("..", "").strip()
    name = re.sub(r'[<>:"|?*]', '', name).strip()
    return name


def _writable_coding_root() -> Optional[Path]:
    """First existing, writable directory configured under workspace.coding_roots."""
    coding_roots = ConfigManager.get("workspace", {}).get("coding_roots", [])
    for root_str in coding_roots:
        root = Path(ConfigManager.expand_path(root_str))
        if root.exists() and root.is_dir():
            return root
    return None


def create_workspace_project(name: str) -> Dict[str, str]:
    """Create (or reuse) a project folder under the first configured coding root."""
    sanitized = _sanitize_project_name(name)
    if not sanitized:
        raise ValueError("Project name must contain at least one valid character.")

    root = _writable_coding_root()
    if root is None:
        raise ValueError("No workspace.coding_roots configured or none exist on disk.")

    new_dir = root / sanitized
    new_dir.mkdir(parents=False, exist_ok=True)
    if not ProjectDiscovery.is_project_dir(new_dir):
        (new_dir / ".chad-project").touch()

    return {"path": str(new_dir.resolve()), "name": new_dir.name}


def rename_workspace_project(old_path: str, new_name: str) -> Dict[str, str]:
    """Rename a workspace project folder on disk. Refuses to touch anything outside a configured coding root."""
    sanitized = _sanitize_project_name(new_name)
    if not sanitized:
        raise ValueError("Project name must contain at least one valid character.")

    old_dir = Path(old_path).resolve()
    coding_roots = [Path(ConfigManager.expand_path(r)).resolve() for r in ConfigManager.get("workspace", {}).get("coding_roots", [])]
    if not old_dir.is_dir() or not any(old_dir.parent == r for r in coding_roots):
        raise ValueError("Refusing to rename a folder outside a configured workspace root.")

    new_dir = old_dir.parent / sanitized
    if new_dir.exists():
        raise ValueError(f"A project named '{sanitized}' already exists.")

    old_dir.rename(new_dir)
    return {"path": str(new_dir.resolve()), "name": new_dir.name}


def list_workspace_projects() -> List[Dict[str, str]]:
    """Discover coding projects under configured workspace roots."""
    projects: List[Dict[str, str]] = []
    seen: set[str] = set()
    coding_roots = ConfigManager.get("workspace", {}).get("coding_roots", [])
    for root_str in coding_roots:
        root = Path(ConfigManager.expand_path(root_str))
        if not root.exists() or not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or not ProjectDiscovery.is_project_dir(entry):
                    continue
                path = str(entry.resolve())
                if path in seen:
                    continue
                seen.add(path)
                projects.append({"path": path, "name": entry.name})
        except PermissionError:
            continue
    return sorted(projects, key=lambda p: p["name"].lower())

@dataclass
class GetConversationsCommand(Command):
    """Command to fetch conversation records with pagination."""
    limit: int = 50
    offset: int = 0
    
    async def execute(self, facade) -> List[Dict[str, Any]]:
        repo = ChatHistoryRepository()
        return await repo.get_conversations(limit=self.limit, offset=self.offset)

@dataclass
class GetMessagesCommand(Command):
    """Command to fetch all messages for a specific conversation."""
    conversation_id: str

    async def execute(self, facade) -> List[Dict[str, Any]]:
        repo = ChatHistoryRepository()
        return await repo.get_messages(self.conversation_id)

@dataclass
class CreateConversationCommand(Command):
    """Command to create a new conversation record, optionally inside a domain agent."""
    model: Optional[str] = None
    title: str = "New Chat"
    domain: Optional[str] = None

    async def execute(self, facade) -> Dict[str, Any]:
        repo = ChatHistoryRepository()
        return await repo.create_conversation(
            model=self.model, title=self.title, domain=self.domain
        )

@dataclass
class UpdateConversationTitleCommand(Command):
    """Command to update a conversation's title."""
    conversation_id: str
    title: str

    async def execute(self, facade) -> bool:
        repo = ChatHistoryRepository()
        return await repo.update_conversation_title(self.conversation_id, self.title)

@dataclass
class DeleteConversationCommand(Command):
    """Command to delete a conversation and its messages."""
    conversation_id: str

    async def execute(self, facade) -> bool:
        repo = ChatHistoryRepository()
        return await repo.delete_conversation(self.conversation_id)

@dataclass
class PinConversationCommand(Command):
    """Command to pin or unpin a conversation in the sidebar."""
    conversation_id: str
    is_pinned: bool

    async def execute(self, facade) -> bool:
        repo = ChatHistoryRepository()
        return await repo.update_conversation_pinned(self.conversation_id, self.is_pinned)

@dataclass
class SetConversationProjectCommand(Command):
    """Command to associate a conversation with a workspace project."""
    conversation_id: str
    project_path: Optional[str] = None

    async def execute(self, facade) -> bool:
        repo = ChatHistoryRepository()
        return await repo.update_conversation_project(self.conversation_id, self.project_path)

@dataclass
class SetConversationDomainCommand(Command):
    """Command to scope a conversation to a domain agent (or clear it)."""
    conversation_id: str
    domain: Optional[str] = None

    async def execute(self, facade) -> bool:
        repo = ChatHistoryRepository()
        return await repo.update_conversation_domain(self.conversation_id, self.domain)


@dataclass
class ListDomainsCommand(Command):
    """Command to list the user's domain agents for the sidebar Categories section."""

    async def execute(self, facade) -> List[Dict[str, Any]]:
        return await DomainRepository().list_domains()


@dataclass
class CreateDomainCommand(Command):
    """Command to create a domain agent from the New Category form."""
    name: str
    description: str = ""
    icon: str = ""
    color: str = ""
    persona: str = ""

    async def execute(self, facade) -> Dict[str, Any]:
        return await DomainRepository().create_domain(
            name=self.name, description=self.description, icon=self.icon,
            color=self.color, persona=self.persona,
        )


@dataclass
class UpdateDomainCommand(Command):
    """Command to edit a domain agent's name/persona/appearance."""
    domain_id: str
    fields: Dict[str, Any]

    async def execute(self, facade) -> bool:
        return await DomainRepository().update_domain(self.domain_id, self.fields)


@dataclass
class DeleteDomainCommand(Command):
    """Command to delete a domain agent definition (its data is left intact)."""
    domain_id: str

    async def execute(self, facade) -> bool:
        return await DomainRepository().delete_domain(self.domain_id)


@dataclass
class ListWorkspaceProjectsCommand(Command):
    """Command to list discoverable workspace projects for the move-to-project menu."""

    async def execute(self, facade) -> List[Dict[str, str]]:
        return list_workspace_projects()

@dataclass
class CreateWorkspaceProjectCommand(Command):
    """Command to create a new workspace project folder (Projects nav, #164)."""
    name: str

    async def execute(self, facade) -> Dict[str, str]:
        return create_workspace_project(self.name)

@dataclass
class RenameWorkspaceProjectCommand(Command):
    """Command to rename a workspace project folder and retarget any conversations pointed at it."""
    old_path: str
    new_name: str

    async def execute(self, facade) -> Dict[str, str]:
        result = rename_workspace_project(self.old_path, self.new_name)
        repo = ChatHistoryRepository()
        await repo.reassign_project_path(self.old_path, result["path"])
        return result

@dataclass
class SearchChatsCommand(Command):
    """Command to search for messages or conversations."""
    query: str
    limit: int = 50

    async def execute(self, facade) -> Dict[str, List[Dict[str, Any]]]:
        repo = ChatHistoryRepository()
        messages = await repo.search_chat_messages(self.query, limit=self.limit)
        conversations = await repo.search_conversations(self.query, limit=self.limit)
        return {
            "messages": messages,
            "conversations": conversations
        }
