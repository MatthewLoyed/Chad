"""
Manage User Memory Tool
Single interface for Chad's personal knowledge graph.
All facts go to/from data/knowledge_graph.json via GraphRAGClient.
"""

from typing import Dict, Any, Optional, List
import asyncio
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("ManageUserMemory")

class ManageUserMemory(BaseTool):
    """Single interface for Chad's personal knowledge graph."""

    name = "manage_user_memory"
    description = (
        "COMMAND: Manage your long-term knowledge about the user. One knowledge graph, "
        "no duplication. Actions:\n"
        "1. save: Store a durable fact about the user (identity, relationships, "
        "preferences, life events, ongoing situations).\n"
        "2. search: Find existing memories by keyword to refresh your context.\n"
        "3. forget: Invalidate a fact by description when it is no longer true.\n"
        "4. status: Summarize what the knowledge graph currently contains.\n"
        "\n"
        "Writing a good memory — one short, complete sentence that will still be true "
        "and still make sense months from now:\n"
        "- One fact per save. Multi-fact text is split automatically, but shorter is better.\n"
        "- Name people and things explicitly; never start with 'he', 'she', 'they', 'it'.\n"
        "- Durable facts only. Tasks, reminders, and 'for now' details are rejected.\n"
        "- Don't check for duplicates first — saving something already known is "
        "detected and skipped, so a save is always safe to attempt.\n"
        "Good: \"Sam's birthday is June 3.\" / \"Prefers morning meetings.\"\n"
        "Bad: \"Remind me to call Sam.\" (a task) / \"They like it.\" (unresolvable later)"
    )
    category = "custom"

    def wants_approval(self, params: Dict[str, Any]) -> bool:
        # Consent must be enforced by the executor, not inferred by the model.
        return params.get("action") in {"save", "forget"}

    def approval_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        verb = "Remember" if params.get("action") == "save" else "Forget"
        return {"prompt": f"{verb} this fact? {params.get('content', '')}", "options": None}
    
    def __init__(self, config: Optional[Dict[str, Any]] = None, **kwargs):
        super().__init__(config=config)
        self.memory = None
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Save, search, forget, or inspect facts about the user. Single knowledge graph — one memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "search", "forget", "status"],
                        "description": "save: add a fact. search: find facts. forget: invalidate a fact by description. status: show graph summary."
                    },
                    "content": {
                        "type": "string",
                        "description": "For 'save': the fact to store. For 'forget': description of what to invalidate."
                    },
                    "query": {
                        "type": "string",
                        "description": "For 'search': keyword or phrase to look for."
                    },
                },
                "required": ["action"]
            }
        }

    async def _get_graph(self):
        from src.core.intelligence.graph_rag_client import GraphRAGClient
        client = GraphRAGClient()
        await client.initialize()
        return client

    async def _save(self, content: str) -> Dict[str, Any]:
        """Gate the candidate, write what survives, and report back instructively.

        The gate does the mechanical work (splitting multi-fact saves, rejecting
        tasks and paragraphs, catching duplicates); the result text tells the model
        exactly what happened and what to do about it, so a bad save is recoverable
        in one retry instead of silently polluting the graph.
        """
        from src.core.intelligence.memory_write_gate import MemoryWriteGate

        client = await self._get_graph()
        decision = await MemoryWriteGate.from_config(client).evaluate(content)

        written = 0
        for fact in decision.accepted:
            if await client.ingest(fact, source="manual"):
                written += 1

        if written:
            logger.info(f"[ MEMORY ] Saved {written} fact(s); skipped {len(decision.skipped)}.")
        elif decision.skipped:
            logger.info(f"[ MEMORY ] Save rejected: {decision.skipped[0][1]}")

        # A save that stored nothing is not an error — "already known" and "that's
        # a task, not a fact" are correct outcomes. Reporting them as failures made
        # the model retry the same rejected text.
        return {
            "success": True,
            "saved_count": written,
            "skipped_count": len(decision.skipped),
            "result": decision.summary(),
            "caption": (
                f"Saved {written} fact(s) to knowledge graph."
                if written else "Nothing new to save."
            ),
        }

    async def execute(self, action: str, content: Optional[str] = None, query: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            if action == "save":
                if not content:
                    return {"success": False, "error": "content is required for save."}
                return await self._save(content)

            elif action == "search":
                if not query:
                    return {"success": False, "error": "query is required for search."}
                client = await self._get_graph()
                raw = await client.search(query)
                results = [l.lstrip("- ").strip() for l in (raw or "").splitlines() if l.strip()]
                return {
                    "success": True,
                    "count": len(results),
                    "results": results,
                    "caption": f"Found {len(results)} facts matching '{query}'.",
                }

            elif action == "forget":
                if not content:
                    return {"success": False, "error": "content (description of fact) is required for forget."}
                client = await self._get_graph()
                outcome = await client.forget(content)
                invalidated = outcome["invalidated"]
                if invalidated:
                    names = "; ".join(f"'{item['content']}'" for item in invalidated)
                    return {
                        "success": True,
                        "result": f"Invalidated: {names}",
                        "invalidated": invalidated,
                        "caption": f"Forgot {len(invalidated)} fact(s).",
                    }
                if outcome["ambiguous"]:
                    named = "; ".join(f"'{c['content']}'" for c in outcome["candidates"])
                    return {
                        "success": False,
                        "result": (
                            f"Not specific enough to forget safely -- closest match(es): {named}. "
                            "Quote the exact fact you want invalidated."
                        ),
                        "candidates": outcome["candidates"],
                        "caption": "Forget was ambiguous.",
                    }
                return {
                    "success": False,
                    "result": "No matching facts found.",
                    "caption": "Nothing to forget.",
                }

            elif action == "status":
                client = await self._get_graph()
                status = client.get_status()
                return {"success": True, "result": status, "caption": "Knowledge graph status."}

            else:
                return {"success": False, "error": f"Invalid action: {action}"}

        except Exception as e:
            logger.error(f"User Memory Error: {e}")
            return {"success": False, "error": str(e)}

    def validate_params(self, action: str = None, **kwargs) -> tuple[bool, str | None]:
        if not action:
            return False, "action is required"
        if action not in {"save", "search", "forget", "status"}:
            return False, "action must be one of: save, search, forget, status"
        return True, None
