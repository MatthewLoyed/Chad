from typing import Dict, Any, Callable
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from src.core.orchestration.nodes.supervisor_node import _inject_turn_context_into_last_turn
from src.shared.logger import setup_logger

logger = setup_logger("ConversationalResponder")


def make_conversational_node(model: Any) -> Callable:
    """Factory creating the conversational responder node with the given LLM model."""
    
    async def conversational_responder(state: Dict[str, Any], config: RunnableConfig):
        """Fast read-only conversational responder.
        
        Binds read tools for 1-hop info retrieval if tools are available and no tool round
        has occurred yet. If a tool round already ran (or no tools are needed), answers textually.
        """
        cloud_persona = (state.get("system_instruction") or "").strip()
        if not cloud_persona:
            cloud_persona = "You are Chad in Fast Chat Mode. Answer questions directly, concisely, and accurately using read-only information tools."
        
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=cloud_persona),
            MessagesPlaceholder(variable_name="messages"),
        ])
        
        # Tools: allow 1-hop execution of read-only tools
        tools = state.get("tools") or []
        chat_tool_rounds = state.get("chat_tool_rounds", 0)
        
        # Ensure thinking is turned off for fast conversational chat
        target_model = model
        if hasattr(model, "copy"):
            target_model = model.copy(update={"thinking_level": "off", "thinking_enabled": False})

        if chat_tool_rounds < 1 and tools and hasattr(target_model, "bind_tools"):
            logger.info(f"[ CHAT ] Binding {len(tools)} read-only tools for chat lane.")
            active_model = target_model.bind_tools(tools)
        else:
            active_model = target_model
            
        chain = prompt | active_model
        
        # Tag this LLM run so the streaming loop knows its tokens are user-facing answer text
        config.setdefault("tags", [])
        if "conversational-llm" not in config["tags"]:
            config["tags"] = list(config.get("tags", [])) + ["conversational-llm"]

        # Inject knowledge_boost into trailing user turn so system prefix KV cache remains intact
        knowledge_boost = state.get("knowledge_boost", "") or ""
        turn_messages = _inject_turn_context_into_last_turn(state.get("messages", []), knowledge_boost)

        response = await chain.ainvoke({"messages": turn_messages}, config=config)
        return {"messages": [response], "iteration_count": 0}

    return conversational_responder
