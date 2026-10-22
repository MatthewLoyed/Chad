"""
Telegram / phone contact front desk (9B).

Mirrors the voice lane: lean tools (escalate_to_agent only), personal ack text,
then optional handoff to the full agent pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger("ContactNode")

CONTACT_TOOL_NAMES = frozenset({"escalate_to_agent"})

def make_contact_node(model: Any, tool_registry: Any) -> Callable:
    """Single-hop contact desk with escalate_to_agent only."""

    async def contact_responder(state: Dict[str, Any], config: RunnableConfig):
        direct_tools = []
        for name in sorted(CONTACT_TOOL_NAMES):
            tool = tool_registry.get_langchain_tool_by_name(name)
            if tool:
                direct_tools.append(tool)
            else:
                logger.warning(
                    f"Contact lane tool '{name}' not found in registry; "
                    "task handoff will not work."
                )

        cloud_persona = state.get("system_instruction", "") or ""
        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=cloud_persona),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        if direct_tools:
            chain = prompt | model.bind_tools(direct_tools)
        else:
            chain = prompt | model

        config.setdefault("tags", [])
        if "contact-llm" not in config["tags"]:
            config["tags"] = list(config.get("tags", [])) + ["contact-llm"]

        response = await chain.ainvoke({"messages": state["messages"]}, config=config)
        iteration = state.get("iteration_count", 0) + 1
        return {"messages": [response], "iteration_count": iteration}

    return contact_responder
