"""
Dry-Run Prompt Inspector Test
Executes Chad's full TurnCoordinator pipeline but intercepts before LLM API calls.
Outputs the compiled system instruction, messages, tools, and token estimation.
"""

import asyncio
import os
import sys
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager
from src.application.events import EventBus
from src.infrastructure.tools.registry import ToolRegistry
from src.core.tools.tool_executor import LocalToolExecutor
from src.core.interfaces.text_mode import TextModeClient
from src.core.intelligence.memory_engine import MemoryEngine
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse, TokenUsage
from src.core.utils.token_counter import TokenCounter
from src.infrastructure.repositories.prompt_registry import PromptRegistry

class SpyAdapter(AIAdapter):
    """
    Spy Adapter that intercepts calls to generate, captures the fully-assembled
    messages and tool schemas, and returns a dummy completion.
    """
    def __init__(self):
        self.model_name = "spy-adapter-model"
        self.captured_messages = []
        self.captured_tools = []
        self.captured_temperature = 0.7
        self.captured_thinking_level = "medium"

    async def is_ready(self) -> bool:
        return True

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Any] = None,
        on_token: Optional[Any] = None,
        agent_name: Optional[str] = None,
        **kwargs
    ) -> GenerateResponse:
        self.captured_messages = messages
        self.captured_tools = tools or []
        self.captured_temperature = temperature
        self.captured_thinking_level = kwargs.get("thinking_level", "medium")

        # Return a quick dummy completion to stop LangGraph's turn cleanly
        return GenerateResponse(
            text="Dry run interception complete. No model called.",
            tool_calls=None,
            finish_reason="stop"
        )

    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": s.get("parameters", {"type": "object", "properties": {}}),
                },
            }
            for s in schemas
            if s.get("name")
        ]

    def build_tool_result_message(self, tool_call_id: str, name: str, result: Any) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": str(result)}

    def build_assistant_tool_call_message(self, tool_calls: List[Dict[str, Any]], content: Optional[str] = None) -> Dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def warmup_mock_registry():
    """Populates the PromptRegistry cache offline for standalone dry-runs."""
    print("[MOCK REGISTRY] Supplying offline fallback persona prompts...")
    registry = PromptRegistry.get_instance()
    prompts = {
        "identity": "You are CHAD, a powerful agentic AI coding assistant designed by Google DeepMind.",
        "soul": "Provide high-quality technical assistance.",
        "user": "Always prioritize the user's specific guidelines.",
        "rich_output": "Provide clear outputs using markdown formatting.",
        "agent": "Specialist agent guidelines and protocols.",
        "tool_calling": "Tool calling specifications: format tools correctly.",
        "mcp_instructions": "Guidelines for utilizing Model Context Protocol (MCP) servers.",
        "heartbeat": "Background daemon task instructions.",
        "heartbeat_protocol": "Heartbeat protocol details.",
        "planner": "You are Chad's Planner. Map out vertical slices.",
        "validator": "You are Chad's Reflection & Quality Validator.",
        "supervisor_routing": "ORCHESTRATION & ROUTING: Coordinate tasks and invoke tools.",
        "subagent_base": "OUTPUT PROTOCOL: Return only the result."
    }
    scopes = {k: "always" if k not in ["planner", "validator", "supervisor_routing"] else k for k in prompts.keys()}
    # Update special scopes
    scopes["planner"] = "planner"
    scopes["validator"] = "validator"
    scopes["supervisor_routing"] = "supervisor"
    
    registry._prompts = prompts
    registry._scopes = scopes
    registry._loaded = True


async def run_dry_run(query: str):
    print("\n" + "="*70)
    print("=== CHAD PROMPT INSPECTOR (DRY-RUN) ===")
    print("="*70)
    print(f"User Turn Query: \"{query}\"\n")

    # 1. Initialize core managers
    ConfigManager.load_config()
    event_bus = EventBus()
    
    # 2. Local-only Tool Registry
    registry = ToolRegistry(event_bus=event_bus)
    registry.discover_all(sync_cloud=False)
    
    # 3. Warm up Prompt Registry
    try:
        print("[STEP] Warming up PromptRegistry from Supabase...")
        await PromptRegistry.get_instance().warmup()
    except Exception as e:
        print(f"[WARNING] Could not connect to Supabase: {e}")
        warmup_mock_registry()

    memory = MemoryEngine()
    spy_adapter = SpyAdapter()
    
    executor = LocalToolExecutor(
        tool_registry=registry,
        memory_engine=memory
    )

    client = TextModeClient(
        adapter=spy_adapter,
        tool_registry=registry,
        tool_executor=executor,
        memory_engine=memory,
        event_bus=event_bus
    )

    # Set up some dummy chat history for testing the pipeline's handling of context
    # In a real run, this would be loaded from ChatHistoryRepository
    from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
    history_repo = ChatHistoryRepository()
    try:
        # Clean session history and add a dummy past turn
        await history_repo.clear_messages("test_dry_run")
        await history_repo.add_message("test_dry_run", "user", "Hello Chad, what is my name?")
        await history_repo.add_message("test_dry_run", "assistant", "Hello! I don't know your name yet. Please tell me.")
    except Exception as e:
        print(f"[INFO] Skipping ChatHistoryRepository setup (using clean memory): {e}")

    # 4. Dispatch turn through TurnCoordinator
    print("[STEP] Running TurnCoordinator dispatch...")
    await client.send_message(
        text=query,
        conversation_id="test_dry_run",
        category="agent"
    )

    # 5. Extract and print intercepted prompt components
    messages = spy_adapter.captured_messages
    tools = spy_adapter.captured_tools

    if not messages:
        print("[ERROR] No messages were captured by the spy adapter!")
        return

    # Extract System Prompt vs other messages
    system_prompt = ""
    history_messages = []
    
    for msg in messages:
        if msg.get("role") == "system":
            system_prompt = msg.get("content", "")
        else:
            history_messages.append(msg)

    # 6. Calculate token estimations
    sys_tokens = TokenCounter.count_text(system_prompt)
    msg_tokens = sum(TokenCounter.count_message(m) for m in history_messages)
    tool_tokens = TokenCounter.count_tools(tools)
    total_tokens = TokenCounter.count_total(
        system_instruction=system_prompt,
        memory_block="",
        history=history_messages,
        tools=tools
    )

    # Print Report
    print("\n" + "-"*70)
    print("[ DETECTED SYSTEM PROMPT ]")
    print("-"*70)
    if system_prompt:
        print(system_prompt)
    else:
        print("[Empty]")
    print(f"\nSystem Prompt Tokens (Est): {sys_tokens}")

    print("\n" + "-"*70)
    print("[ MESSAGES SENT TO MODEL ]")
    print("-"*70)
    for idx, msg in enumerate(history_messages):
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        token_est = TokenCounter.count_message(msg)
        print(f"[{idx+1}] {role}:")
        print(f"  {content}")
        print(f"  Tokens (Est): {token_est}\n")

    print("-"*70)
    print(f"[ BOUND TOOLS ] ({len(tools)} active)")
    print("-"*70)
    for t in tools:
        t_name = t.get("name") or t.get("function", {}).get("name")
        t_desc = t.get("description") or t.get("function", {}).get("description", "")
        print(f"- {t_name}: {t_desc}")
    print(f"\nTools Schema Tokens (Est): {tool_tokens}")

    print("\n" + "="*70)
    print("[ TOKEN BUDGET ESTIMATES ]")
    print("="*70)
    print(f"  System Instruction:  {sys_tokens:>6} tokens")
    print(f"  Messages / History:  {msg_tokens:>6} tokens")
    print(f"  Tool Definitions:    {tool_tokens:>6} tokens")
    print(f"  -------------------------------------")
    print(f"  PROJECTED TOTAL:     {total_tokens:>6} tokens")
    print("="*70)

    # 7. Dump full json data for review
    output_dir = Path("tests/data")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "dry_run_prompt.json"
    
    run_output = {
        "user_query": query,
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "token_metrics": {
            "system_tokens": sys_tokens,
            "message_history_tokens": msg_tokens,
            "tool_tokens": tool_tokens,
            "total_projected_tokens": total_tokens
        },
        "system_instruction": system_prompt,
        "messages": history_messages,
        "bound_tools": tools
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(run_output, f, indent=2)
    print(f"\n[INFO] Full prompt dump successfully written to {output_file}\n")


if __name__ == "__main__":
    os.environ["CHAD_TESTING"] = "true"
    # Ensure Windows selector policy is used for asyncio compatibility
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # Reconfigure sys.stdout to handle UTF-8 if needed on Windows
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass
        
    test_query = "Write a python script to calculate the area of a circle and save it."
    if len(sys.argv) > 1:
        test_query = " ".join(sys.argv[1:])
        
    asyncio.run(run_dry_run(test_query))
