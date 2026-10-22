"""
Capability suggestion commands.

Relay the desktop "Suggestions" panel's reads/writes to the Brain. These commands
call the standalone async helpers in ``src.core.intelligence.capability_gaps``
directly (the data is a simple Supabase table, not facade state), mirroring how
other lightweight data commands are dispatched through ``/api/command``.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from .base_command import Command


@dataclass
class GetCapabilitySuggestionsCommand(Command):
    """Fetch the pending capability suggestions queue for the side panel."""

    limit: int = 5

    async def execute(self, facade) -> List[Dict[str, Any]]:
        from src.core.intelligence.capability_gaps import get_pending_suggestions
        return await get_pending_suggestions(limit=int(self.limit))

    def __str__(self) -> str:
        return f"GetCapabilitySuggestionsCommand(limit={self.limit})"


@dataclass
class SetCapabilitySuggestionStatusCommand(Command):
    """Accept or dismiss a capability suggestion (status transition only)."""

    suggestion_id: str
    status: str  # 'accepted' | 'dismissed' | 'shown'

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.suggestion_id:
            return False, "suggestion_id is required"
        if self.status not in {"accepted", "dismissed", "shown", "pending"}:
            return False, f"Invalid status: {self.status}"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.capability_gaps import set_suggestion_status
        ok = await set_suggestion_status(self.suggestion_id, self.status)
        return {"success": ok}

    def __str__(self) -> str:
        return f"SetCapabilitySuggestionStatusCommand(id={self.suggestion_id}, status={self.status})"


@dataclass
class ProposeCapabilitySkillBuildCommand(Command):
    """Drafts agent.py + SKILL.md for an accepted SKILL suggestion and mints a one-time
    approval token. Writes nothing to disk — the human must confirm via the returned
    token before CommitCapabilitySkillBuildCommand actually builds it."""

    suggestion_id: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.suggestion_id:
            return False, "suggestion_id is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.capability_gaps import get_suggestion_by_id, set_suggestion_status
        from src.core.intelligence.capability_skill_builder import propose_skill_write

        cand = await get_suggestion_by_id(self.suggestion_id)
        if not cand:
            return {"success": False, "error": f"Suggestion {self.suggestion_id} not found"}
        if (cand.get("kind") or "").lower() != "skill":
            return {"success": False, "error": "Only 'skill' suggestions can be auto-built"}

        result = propose_skill_write(cand)
        if result.get("success"):
            await set_suggestion_status(self.suggestion_id, "accepted")
        # Strip rendered source from the GUI payload — the markdown preview already
        # names the files that would be created; no need to ship the source over the wire.
        result.pop("files", None)
        return result

    def __str__(self) -> str:
        return f"ProposeCapabilitySkillBuildCommand(id={self.suggestion_id})"


@dataclass
class CommitCapabilitySkillBuildCommand(Command):
    """Consumes the approval token from ProposeCapabilitySkillBuildCommand and, only
    then, writes the scaffolded skill to disk + re-scans so it's live."""

    suggestion_id: str
    approval_token: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.suggestion_id:
            return False, "suggestion_id is required"
        if not self.approval_token:
            return False, "approval_token is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.capability_gaps import mark_suggestion_built, mark_suggestion_build_failed
        from src.core.intelligence.capability_skill_builder import commit_skill_write

        result = await commit_skill_write(self.approval_token)
        if result.get("success"):
            await mark_suggestion_built(
                self.suggestion_id,
                skill_name=result.get("skill_name", ""),
                written_files=result.get("written", []))
        else:
            await mark_suggestion_build_failed(self.suggestion_id, error=result.get("error", "unknown error"))
        return result

    def __str__(self) -> str:
        return f"CommitCapabilitySkillBuildCommand(id={self.suggestion_id})"
