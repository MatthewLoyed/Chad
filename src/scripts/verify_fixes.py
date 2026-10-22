
import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.infrastructure.tools import ToolRegistry
from src.tools.system.system_monitor import SystemMonitorTool

async def verify_tools():
    print("--- Verifying Tool Registration ---")
    registry = ToolRegistry()
    registry.discover_all()
    
    monitor_tool = registry.get_tool("system_monitor")
    if monitor_tool:
        print("PASS: SystemMonitorTool registered successfully.")
        print(f"Tool Schema: {monitor_tool.get_schema()['name']}")
    else:
        print("FAIL: SystemMonitorTool NOT found in registry.")

    reminder_tool = registry.get_tool("schedule_reminder")
    if reminder_tool:
        print("PASS: ScheduleReminderTool found.")
    else:
        print("FAIL: ScheduleReminderTool NOT found.")

    print("\n--- Verifying Monitoring Logic ---")
    # Simulate action
    result = await monitor_tool.execute(action="status")
    if result.get('success'):
        print(f"Result: {result['result']}")
    else:
        print(f"Expected Error (Offline Mode): {result.get('error')}")
    
if __name__ == "__main__":
    asyncio.run(verify_tools())
