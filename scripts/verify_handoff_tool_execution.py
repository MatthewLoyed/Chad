import sys
import os
import asyncio
from typing import Any, Dict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

# Set paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.orchestration.handoffs import create_handoff_tools

# Mock sub-agent that captures its input
class CapturingMockAgent:
    def __init__(self):
        self.captured_input = None
        self.captured_config = None
    
    async def ainvoke(self, input_data: Any, config: Any = None, **kwargs):
        self.captured_input = input_data
        self.captured_config = config
        # Return simple mock response
        return {"messages": [HumanMessage(content="Mock sub-agent complete.")]}

async def run_validation():
    print("\n=== HANDOFF TOOL CONTEXT PROPAGATION TEST ===")
    
    # 1. Create capturing mock agent
    mock_agent = CapturingMockAgent()
    agents_dict = {"browser_agent": mock_agent}
    
    # 2. Create the handoff tools
    print("[1] Compiling handoff tools...")
    tools = create_handoff_tools(agents_dict)
    browser_tool = next((t for t in tools if t.name == "delegate_to_browser_agent"), None)
    
    assert browser_tool is not None, "[-] Handoff tool delegate_to_browser_agent was not created!"
    print("[+] Successfully retrieved delegate_to_browser_agent tool.")
    
    # 3. Construct sample supervisor system instruction
    sample_sys_instr = """### PERSONA COMPONENT: IDENTITY (Source: Cloud Master)
You are CHAD.

### PERSONA COMPONENT: USER (Source: Cloud Master)
Name: Alex Example
Timezone: CST

### PERSONA COMPONENT: PROMPT_INJECTION_DEFENSE (Source: Cloud Master)
*** BROWSER SECURITY PROTOCOL ***

### PERSONAL CONTEXT
- User prefers concise answers with the conclusion first
- Primary dev machine runs Windows 11
"""
    
    # 4. Build RunnableConfig with the system instruction
    config = RunnableConfig(
        configurable={
            "system_instruction": sample_sys_instr,
            "loaded_skill": None,
            "loaded_skill_name": None
        },
        recursion_limit=10
    )
    
    # 5. Invoke tool
    test_task = "Check the browser history for anime visits."
    print(f"[2] Invoking handoff tool with task: '{test_task}'...")
    
    # StructeredTool coroutine is invoked via .ainvoke
    await browser_tool.ainvoke({"task": test_task}, config=config)
    
    # 6. Analyze captured inputs to sub-agent
    print("\n=== ANALYZING CAPTURED SUB-AGENT INPUTS ===")
    subagent_input = mock_agent.captured_input
    assert subagent_input is not None, "[-] The sub-agent was never called!"
    
    messages = subagent_input.get("messages", [])
    assert len(messages) > 0, "[-] Subagent did not receive any messages!"
    
    system_msg = next((m for m in messages if isinstance(m, SystemMessage)), None)
    assert system_msg is not None, "[-] Subagent did not receive a SystemMessage!"
    
    content = system_msg.content
    print("\n--- Captured System Message Content Preview ---")
    # Ensure we avoid console charmap errors
    safe_content = str(content).encode('ascii', errors='replace').decode('ascii')
    print(safe_content)
    print("-----------------------------------------------\n")
    
    # Assertions
    print("[3] Asserting context propagation...")
    
    assert "### PERSONAL CONTEXT" in content, "[-] Personal context block NOT found!"
    print("[+] Verified: Personal context block successfully propagated!")
    
    assert "Name: Alex Example" in content, "[-] User Profile NOT found!"
    print("[+] Verified: User profile successfully propagated!")
    
    assert "BROWSER SECURITY PROTOCOL" in content, "[-] Prompt injection defense NOT found!"
    print("[+] Verified: Web security defenses successfully propagated!")
    
    # Verify NO bleed-over of IDENTITY
    assert "### PERSONA COMPONENT: IDENTITY" not in content, "[-] Leak detected: Supervisor Identity was propagated!"
    print("[+] Verified: No identity leak (clean separation maintained).")
    
    print("\n=== SUCCESS: THE KNOWLEDGE LAYER IS FLAWLESSLY PASSED TO SUB-AGENTS! ===")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_validation())
