import asyncio
import os
import sys
import logging

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir))) # core/skill_system -> core -> src -> root
sys.path.insert(0, project_root)

# MOCK TextModeClient directly in the interfaces.text_mode module to avoid API calls and Key validation
import src.core.interfaces.text_mode
class MockClient:
    def __init__(self, **kwargs):
        self.tool_registry = kwargs.get('tool_registry')
    
    async def send_message(self, text, max_tool_calls=15, thinking_level='high'):
        print(f"    [MOCK CLIENT] Received: {text[:100]}...")
        # Verify tools are registered
        if self.tool_registry:
            tools = self.tool_registry.get_all_tools()
            print(f"    [MOCK CLIENT] Verifying Registry: Found {len(tools)} tools.")
        return {
            "success": True, 
            "response": "MOCK_HOMEWORK_RESULT",
            "tool_call_count": 0
        }

src.core.interfaces.text_mode.TextModeClient = MockClient

from src.skills import HomeworkAssistantSkill
from src.shared.logger import setup_logger

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = setup_logger("SkillVerifier")
logging.getLogger("ContextCache").setLevel(logging.CRITICAL) 
logging.getLogger("TextMode").setLevel(logging.CRITICAL) 

async def verify_skill():
    print("--- STARTING SKILL FRAMEWORK VERIFICATION ---")

    # 1. Instantiate the Skill
    print("[1] Instantiating HomeworkAssistantSkill...")
    try:
        skill = HomeworkAssistantSkill(config={"model_name": "gemini-2.0-flash"})
        print(f"    SUCCESS. Skill Name: {skill.name}")
    except Exception as e:
        print(f"    FAILED: {e}")
        import traceback
        traceback.print_exc()
        return

    # 2. Execute the Skill
    print("\n[2] Executing Skill (Simulating Main Agent Call)...")
    print("    Input: 'Help me draft an outline for a history paper on the printing press.'")
    
    try:
        result = await skill.execute(
            input_text="Help me draft an outline for a history paper on the printing press."
        )
        
        print("\n[3] Execution Complete.")
        if result["success"]:
            print("    STATUS: SUCCESS")
            print("    RESULT:")
            print("    " + "-"*40)
            print(result["result"])
            print("    " + "-"*40)
            print(f"    METADATA: {result.get('state')}")
        else:
            print("    STATUS: FAILED")
            print(f"    ERROR: {result.get('error')}")
            
    except Exception as e:
        print(f"    CRITICAL FAILURE during execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify_skill())
