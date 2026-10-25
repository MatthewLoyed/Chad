import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional
from langchain_core.runnables import RunnableConfig

from src.core.orchestration.context import domain_var
from src.shared.domain_scope import normalize_domain

logger = logging.getLogger("ChadSupervisor")


def is_conversational(messages: list) -> bool:
    """Cheap heuristic — runs before LLM, no tokens spent."""
    if not messages:
        return False

    last = messages[-1]
    # Handle different message types safely
    content = ""
    if hasattr(last, "content") and isinstance(last.content, str):
        content = last.content
    elif isinstance(last, str):
        content = last
    else:
        content = str(last)

    # CRITICAL: Never fast-track multimodal messages. They need the full supervisor
    # reasoning loop to determine if they need vision-capable agents or OCR.
    if hasattr(last, "additional_kwargs") and (
        last.additional_kwargs.get("content_blocks") or
        last.additional_kwargs.get("image_base64")
    ):
        logger.info("[ ROUTE ] Multimodal content detected. Forcing supervisor path.")
        return False

    content = content.strip()

    CONVERSATIONAL_TRIGGERS = {
        "hey", "hi", "hello", "thanks", "thank you", "ok", "okay",
        "cool", "got it", "sounds good", "bye", "lol", "haha", "sup",
        "what's up", "whats up", "yo", "nice", "great", "awesome"
    }

    words = content.lower().split()
    is_short = len(words) <= 6
    is_greeting = any(w.strip("!?.") in CONVERSATIONAL_TRIGGERS for w in words)
    has_no_action_words = not any(w in content.lower() for w in [
        "find", "search", "create", "update", "delete", "analyze",
        "research", "list", "get", "fetch", "send", "schedule", "make", "write",
        "turn", "on", "off", "open", "close", "play", "stop", "pause",
        "set", "check", "run", "execute", "show", "tell", "what", "how"
    ])

    return is_short and (is_greeting or has_no_action_words)


def _message_text(message: Any) -> str:
    if message is None:
        return ""
    if hasattr(message, "content") and isinstance(message.content, str):
        return message.content.strip()
    if isinstance(message, str):
        return message.strip()
    return str(message).strip()


def _is_user_message(message: Any) -> bool:
    role = getattr(message, "type", None) or getattr(message, "role", None)
    if isinstance(role, str) and role.lower() in {"human", "user"}:
        return True
    return message.__class__.__name__ in {"HumanMessage", "UserMessage"}


def build_knowledge_query(messages: list, *, max_prior_user: int = 2) -> str:
    """
    Retrieval query for graph/activity/drive.

    Follow-ups like "what's the next step on our roadmap" need prior-turn
    nouns (fiancée → wedding → roadmap doc). Blend recent user turns when the
    current message is short or anaphoric — no LLM.
    """
    if not messages:
        return ""

    current = _message_text(messages[-1])
    priors: List[str] = []
    for message in reversed(messages[:-1]):
        if not _is_user_message(message):
            continue
        text = _message_text(message)
        if not text:
            continue
        priors.append(text)
        if len(priors) >= max_prior_user:
            break

    if not priors:
        return current

    folded = current.lower()
    words = set(re.findall(r"[a-z0-9]+", folded))
    anaphora = words & {
        "our", "we", "us", "it", "that", "this", "they", "them", "those",
        "these", "same", "next", "previous", "again",
    }
    underspecified = (
        len(words) <= 14
        or bool(anaphora)
        or any(k in folded for k in ("roadmap", "next step", "the doc", "the folder", "drive"))
    )
    if not underspecified:
        return current

    # Prior turns first so embeddings still see wedding/Sam, then the ask.
    return " ".join(list(reversed(priors)) + [current])


def format_knowledge_boost(by_label: Dict[str, Any]) -> str:
    """Turn parallel search hits into the labeled knowledge_boost string."""
    boost_block = ""

    if by_label.get("graph"):
        logger.info("[ GRAPH ] Injecting relational context.")
        boost_block += (
            "\n\n### RELATIONAL CONTEXT (GRAPH)\n"
            "Optional personal facts from your knowledge graph. Use only items that "
            "help accomplish the user's current task — ignore the rest; not every "
            "item will be relevant:\n"
            f"{by_label['graph']}\n"
        )

    if by_label.get("activity"):
        logger.info("[ ACTIVITY ] Injecting activity context.")
        boost_block += (
            "\n\n### ACTIVITY CONTEXT (PERSONAL INDEX)\n"
            "Optional recent browsing, email, and Drive activity. Reference only "
            "what helps with the current request; skip unrelated noise:\n"
            f"{by_label['activity']}\n"
        )

    if by_label.get("drive"):
        logger.info("[ DRIVE_CATALOG ] Injecting document context.")
        boost_block += (
            "\n\n### DOCUMENT CONTEXT (DRIVE)\n"
            "Optional Google Drive files/folders (name + path + id). Location only — "
            "not file body. To read contents, call google_drive_read_file with the "
            "id= value (or the share URL). Ignore unrelated hits:\n"
            f"{by_label['drive']}\n"
        )

    if by_label.get("session"):
        logger.info("[ SESSION_HISTORY ] Injecting past-session context.")
        boost_block += (
            "\n\n### SESSION HISTORY\n"
            "Optional exchanges from past conversations with the user. Use only if "
            "directly relevant to what they're asking now (e.g. 'what did we decide "
            "about X', continuing earlier work); ignore if unrelated:\n"
            f"{by_label['session']}\n"
        )

    if by_label.get("calendar"):
        logger.info("[ CALENDAR ] Injecting upcoming schedule context.")
        boost_block += f"\n\n{by_label['calendar']}\n"

    if by_label.get("grounding"):
        logger.info("[ ENTITY_GROUNDING ] Injecting verified web entity context.")
        boost_block += (
            "\n\n### 🌐 GROUNDED WEB SERVICES & APPS\n"
            "Verified web applications, websites, and external services referenced in the request "
            "(interact using browser tools / delegate_to_browser_agent; not local files or Drive):\n"
            f"{by_label['grounding']}\n"
        )

    return boost_block


async def fetch_knowledge_boost(
    query: str,
    graph_client: Any,
    activity_client: Optional[Any] = None,
    drive_client: Optional[Any] = None,
    session_client: Optional[Any] = None,
    domain: Optional[str] = None,
    calendar_client: Optional[Any] = None,
    entity_client: Optional[Any] = None,
) -> str:
    """
    Query knowledge clients in parallel and return the labeled boost string.

    Shared by tool selection (so the 9B can infer Gmail/Drive tools from personal
    context) and graphrag_injector_node (so the supervisor sees the same block).
    Client failures are logged and skipped — never raise.

    `domain` scopes every client to the turn's domain agent, defaulting to the active
    turn's scope. This is THE choke point where domain isolation is enforced for the
    supervisor lane: a client added below without a `domain=` argument silently retrieves
    across every domain, so pass it to anything that returns personal content.
    """
    if not query or not query.strip():
        return ""

    domain = normalize_domain(domain if domain is not None else domain_var.get())

    tasks: list = [graph_client.search(query, domain=domain)]
    task_labels = ["graph"]
    if activity_client:
        tasks.append(activity_client.search(query, domain=domain))
        task_labels.append("activity")
    if drive_client:
        tasks.append(drive_client.search(query, domain=domain))
        task_labels.append("drive")
    if session_client:
        tasks.append(session_client.search(query, domain=domain))
        task_labels.append("session")
    if calendar_client:
        tasks.append(calendar_client.search(query, domain=domain))
        task_labels.append("calendar")
    if entity_client:
        tasks.append(entity_client.search(query, domain=domain))
        task_labels.append("grounding")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    by_label: Dict[str, Any] = {}
    for label, result in zip(task_labels, results):
        if isinstance(result, Exception):
            logger.error(f"[ {label.upper()} ] Search error: {result}")
            by_label[label] = None
        else:
            by_label[label] = result

    return format_knowledge_boost(by_label)


def make_graphrag_node(
    graph_client: Any,
    activity_client: Optional[Any] = None,
    drive_client: Optional[Any] = None,
    session_client: Optional[Any] = None,
    calendar_client: Optional[Any] = None,
    entity_client: Optional[Any] = None,
) -> Callable:
    """
    Factory creating the knowledge-layer injector node.

    Knowledge clients queried in parallel on every non-chat turn:
      - graph_client   : GraphRAGClient  — local NetworkX curated personal facts
      - activity_client: ActivityRAGClient — Supabase personal_index activity logs
      - drive_client   : DriveCatalogClient — Drive file/folder location catalog
      - session_client : SessionHistoryClient — past conversation exchanges (session_log)
      - calendar_client: CalendarScheduleClient — Google Calendar upcoming events & classes
      - entity_client  : EntityGroundingClient — pre-turn grounding of web services & apps

    To add another knowledge source, add it as a parameter here, include it in the
    asyncio.gather() call below, and give it its own labeled block in boost_block.
    See src/core/intelligence/activity_rag_client.py for the full architecture guide.
    """

    async def graphrag_injector_node(state: Dict[str, Any], config: RunnableConfig):
        """
        Entry interceptor — injects personal knowledge into the system prompt
        before routing to any model logic.
        """
        messages = state.get("messages", [])
        current_instruction = state.get("system_instruction", "")

        if state.get("category") in ("voice", "feed"):
            return {"system_instruction": current_instruction}

        # Tool selection may have already fetched this boost — reuse it so we don't
        # pay a second parallel knowledge round-trip on the same query.
        preseeded = (state.get("knowledge_boost") or "").strip()
        if preseeded:
            logger.info("[ KNOWLEDGE ] Reusing pre-seeded knowledge_boost from tool selection.")
            return {"knowledge_boost": state.get("knowledge_boost") or ""}

        query = build_knowledge_query(messages)
        logger.info(f"[ KNOWLEDGE ] Querying knowledge layer for: '{query[:80]}...'")

        boost_block = await fetch_knowledge_boost(
            query,
            graph_client,
            activity_client=activity_client,
            drive_client=drive_client,
            session_client=session_client,
            calendar_client=calendar_client,
            entity_client=entity_client,
        )

        if boost_block:
            # Return the boost in a SEPARATE field rather than appending it to the
            # system prompt. The system prompt precedes conversation history, so any
            # per-turn-volatile content there forces the local model to re-prefill the
            # entire history every turn (KV-cache miss). The supervisor injects this
            # into the trailing user turn — the uncached tail — instead.
            return {"knowledge_boost": boost_block}

        logger.debug("[ KNOWLEDGE ] No relevant context found for this query.")
        return {"knowledge_boost": ""}

    return graphrag_injector_node
