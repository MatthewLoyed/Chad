import logging
from typing import Any, Callable, Dict

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger("VoiceNode")

VOICE_TOOL_NAMES = frozenset({"escalate_to_agent", "hubspace_control"})

def make_voice_node(model: Any, tool_registry: Any) -> Callable:
    """Single-hop voice responder with Hubspace + escalate tools only.

    Issue #654 problem 3: the no-LLM quick-command fast lane lives entirely in
    `TurnPipeline._try_voice_fast_lane` (`turn/pipeline.py`), which runs before a
    voice turn ever reaches this node — every `category="voice"` turn goes through
    that check first, so a node-level duplicate here would only ever re-confirm a
    miss the pipeline already found, at the cost of re-running the (up to ~1s)
    embedding-tier match a second time on every escalating command. Don't add one.
    """

    async def voice_responder(state: Dict[str, Any], config: RunnableConfig):
        # The voice lane's tool set is FIXED, deliberately independent of the
        # hybrid RAG shortlist: the shortlist is query-driven, so for a request
        # like "check my email inbox" it surfaces Gmail tools and no
        # escalate_to_agent. Binding only what the shortlist happened to return
        # meant the model was told to escalate while having no tool bound to do
        # it with — so it fabricated an answer instead of handing off.
        direct_tools = []
        for name in sorted(VOICE_TOOL_NAMES):
            tool = tool_registry.get_langchain_tool_by_name(name)
            if tool:
                direct_tools.append(tool)
            else:
                logger.warning(
                    f"Voice lane tool '{name}' not found in registry; "
                    "voice turns needing it will not be able to hand off."
                )

        prompt_content = state.get("system_instruction", "")
        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=prompt_content),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        if direct_tools:
            chain = prompt | model.bind_tools(direct_tools)
        else:
            chain = prompt | model

        # Tag this LLM run so langgraph_engine's streaming loop knows its tokens are
        # user-facing answer text (mirrors conversational_node/supervisor_node) — without
        # this tag, voice replies never stream and only reach the user as one final blob.
        config.setdefault("tags", [])
        if "voice-llm" not in config["tags"]:
            config["tags"] = list(config.get("tags", [])) + ["voice-llm"]

        response = await chain.ainvoke({"messages": state["messages"]}, config=config)
        iteration = state.get("iteration_count", 0) + 1
        return {"messages": [response], "iteration_count": iteration}

    return voice_responder
