"""Conversion between Chad's internal dict message format and LangChain messages.

Extracted from langgraph_engine.py (#302).
"""
import json
from typing import Dict, Any, List

from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage


def to_chad_messages(messages: List[AnyMessage]) -> List[Dict[str, Any]]:
    """Convert LangChain messages to Chad's internal dict format."""
    chad_msgs = []
    for m in messages:
        if isinstance(m, HumanMessage):
            msg = {"role": "user", "content": m.content}
            if hasattr(m, "additional_kwargs"):
                if "content_blocks" in m.additional_kwargs:
                    msg["content_blocks"] = m.additional_kwargs["content_blocks"]
                if "image_base64" in m.additional_kwargs:
                    msg["image_base64"] = m.additional_kwargs["image_base64"]
            chad_msgs.append(msg)
        elif isinstance(m, AIMessage):
            msg = {"role": "assistant", "content": m.content}
            if hasattr(m, "tool_calls") and m.tool_calls:
                chad_tool_calls = []
                for tc in m.tool_calls:
                    chad_tool_calls.append({
                        "id": tc.get("id", f"call_{tc['name']}"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": (
                                json.dumps(tc.get("args", {}))
                                if isinstance(tc.get("args"), dict)
                                else tc.get("args", "{}")
                            )
                        }
                    })
                msg["tool_calls"] = chad_tool_calls
            chad_msgs.append(msg)
        elif isinstance(m, ToolMessage):
            chad_msgs.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id,
                "name": m.name,
                "content": m.content
            })
        elif isinstance(m, SystemMessage):
            chad_msgs.append({"role": "system", "content": m.content})
    return chad_msgs


def from_chad_history(history: List[Dict[str, Any]]) -> List[AnyMessage]:
    """Convert Chad's history dicts to LangChain messages.

    Stamps each message with its DB row id so LangGraph's add_messages reducer
    can deduplicate by id — a hard backstop against double-seeding during crash
    recovery or any future refactor that touches persistence ordering.
    """
    lc_msgs = []
    for m in history:
        role = m.get("role")
        content = m.get("content", "")
        content_blocks = m.get("content_blocks")
        image_base64 = m.get("image_base64")
        # Use the DB row id as the stable message id so add_messages can deduplicate.
        db_id = str(m["id"]) if m.get("id") else None

        if role == "user":
            kwargs = {}
            if content_blocks:
                kwargs["content_blocks"] = content_blocks
            if image_base64:
                kwargs["image_base64"] = image_base64
            lc_msgs.append(HumanMessage(content=content, additional_kwargs=kwargs, id=db_id))
        elif role == "assistant":
            ai_m = AIMessage(content=content, id=db_id)
            if "tool_calls" in m:
                lc_tool_calls = []
                for tc in m["tool_calls"]:
                    func = tc.get("function", {})
                    args_raw = func.get("arguments", "{}")
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except Exception:
                        args = {}
                    lc_tool_calls.append({
                        "name": func.get("name", ""),
                        "args": args,
                        "id": tc.get("id", "")
                    })
                ai_m.tool_calls = lc_tool_calls
            lc_msgs.append(ai_m)
        elif role == "tool":
            lc_msgs.append(ToolMessage(
                content=str(content),
                tool_call_id=m.get("tool_call_id", ""),
                name=m.get("name", ""),
                id=db_id
            ))
        elif role == "system":
            lc_msgs.append(SystemMessage(content=content, id=db_id))
    return lc_msgs
