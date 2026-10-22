"""Inputs to tool selection: knowledge prefetch and local-model tool stability.

Two concerns that bracket ``TurnToolSelector`` rather than living inside it:

- **Knowledge prefetch** — the same boost the supervisor injector uses, fetched
  *once* per turn so selection retries don't re-query the knowledge layer.
- **Sticky local tools** — an LRU per session that keeps the bound tool set from
  churning turn-to-turn, which would otherwise invalidate the KV-cache prefix on
  local backends (#38).

The sticky map is long-lived, session-keyed, and owned here. That is legitimate
state for this class (it is the whole point of it), as opposed to the per-turn
scratch values that used to accumulate on the coordinator.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger

logger = setup_logger("TurnToolPreparation")

# Cap on the sticky local-tool set — bounds prompt growth so pinning tools for
# cache stability can't bloat the prompt unchecked.
STICKY_LOCAL_TOOLS_CAP = 40
# Cap on the number of sessions tracked — a coordinator instance lives for the
# server's uptime, so without a cap the outer dict would grow one entry per
# session forever on an always-on deployment.
STICKY_LOCAL_SESSIONS_CAP = 200

# Lanes that never get a knowledge boost: they answer from the model directly.
_NO_KNOWLEDGE_CATEGORIES = ("voice",)


class TurnToolPreparation:
    """Knowledge prefetch + per-session tool-set stabilization."""

    def __init__(
        self,
        tool_registry: Optional[Any] = None,
        tools_cap: int = STICKY_LOCAL_TOOLS_CAP,
        sessions_cap: int = STICKY_LOCAL_SESSIONS_CAP,
    ):
        self._tool_registry = tool_registry
        self._sticky_local_tools: "OrderedDict[str, OrderedDict[str, Any]]" = OrderedDict()
        self._tools_cap = tools_cap
        self._sessions_cap = sessions_cap

    async def fetch_knowledge(
        self,
        user_input: str,
        *,
        history: Optional[List[Dict[str, Any]]] = None,
        category: str = "autonomous",
        turn_mode: Optional[str] = None,
    ) -> str:
        """Fetch the knowledge boost so the model can infer context from personal
        knowledge (graph, activity, drive, entity grounding). Failures are non-fatal (empty string).
        """
        if category in _NO_KNOWLEDGE_CATEGORIES:
            return ""
        try:
            from langchain_core.messages import HumanMessage
            from src.core.orchestration.nodes.graphrag_node import (
                build_knowledge_query,
                fetch_knowledge_boost,
            )
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            from src.core.intelligence.activity_rag_client import ActivityRAGClient
            from src.core.intelligence.drive_catalog_client import DriveCatalogClient
            from src.core.intelligence.session_history_client import SessionHistoryClient
            from src.core.intelligence.calendar_schedule_client import CalendarScheduleClient
            from src.core.intelligence.entity_grounding_client import EntityGroundingClient

            messages = []
            for item in history or []:
                if isinstance(item, dict):
                    role = (item.get("role") or "").lower()
                    content = item.get("content") or ""
                    if role in {"user", "human"} and content:
                        messages.append(HumanMessage(content=str(content)))
                else:
                    messages.append(item)
            if user_input:
                messages.append(HumanMessage(content=str(user_input)))

            query = build_knowledge_query(messages)
            if not query:
                return ""

            logger.info(f"[ TURN ] Prefetching knowledge for tool selection: '{query[:80]}...'")
            entity_client = EntityGroundingClient(self._tool_registry)
            boost = await fetch_knowledge_boost(
                query,
                GraphRAGClient(),
                activity_client=ActivityRAGClient(),
                drive_client=DriveCatalogClient(),
                session_client=SessionHistoryClient() if turn_mode != "chat" else None,
                calendar_client=CalendarScheduleClient(),
                entity_client=entity_client,
            )
            if boost:
                logger.info(
                    f"[ TURN ] Knowledge prefetch ready ({len(boost)} chars) for tool selection."
                )
            return boost or ""
        except Exception as e:
            logger.warning(f"[ TURN ] Knowledge prefetch for tool selection failed (non-fatal): {e}")
            return ""

    def stabilize_local_tools(self, session_id: str, schemas: List[Any]) -> List[Any]:
        """Merge this turn's RAG-selected tools into a per-session sticky set instead of
        replacing it outright, so the bound tool list — and therefore the rendered tool
        schema block local inference servers prefix the prompt with — stays stable
        across turns instead of churning on every message.

        Tools explicitly selected for the current turn are ALWAYS preserved. If the
        sticky set exceeds the cap, stale tools from prior turns that were NOT selected
        this turn fall off first in LRU order.
        """
        if session_id in self._sticky_local_tools:
            self._sticky_local_tools.move_to_end(session_id)
            sticky = self._sticky_local_tools[session_id]
        else:
            sticky = OrderedDict()
            self._sticky_local_tools[session_id] = sticky
            while len(self._sticky_local_tools) > self._sessions_cap:
                self._sticky_local_tools.popitem(last=False)

        current_names = set()
        for schema in schemas:
            fn = schema.get("function") if isinstance(schema, dict) else None
            name = (fn or {}).get("name") or (schema.get("name") if isinstance(schema, dict) else None)
            if not name:
                continue
            current_names.add(name)
            sticky[name] = schema
            sticky.move_to_end(name)

        # Evict stale tools from previous turns (keys not in current_names) if over cap
        effective_cap = max(self._tools_cap, len(current_names))
        if len(sticky) > effective_cap:
            keys_to_evict = [k for k in sticky if k not in current_names]
            while len(sticky) > effective_cap and keys_to_evict:
                evict_key = keys_to_evict.pop(0)
                sticky.pop(evict_key, None)

        return list(sticky.values())


__all__ = [
    "TurnToolPreparation",
    "STICKY_LOCAL_TOOLS_CAP",
    "STICKY_LOCAL_SESSIONS_CAP",
]
