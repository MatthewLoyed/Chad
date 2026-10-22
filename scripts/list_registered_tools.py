import sys
import os
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.application.system_factory import SystemFactory

def list_tools():
    config = {
        "active_profile": "default",
        "mcp_servers": {}
    }
    container = SystemFactory.build_core_engine(config)
    reg = container.tool_registry
    
    print("\n=== ALL REGISTERED TOOLS & DOMAINS ===")
    domains = {}
    for name, tool in reg.tools.items():
        # Print tool details
        dom = getattr(tool, "domain", "unknown")
        domains.setdefault(dom, []).append(name)
        
    for dom, tool_list in sorted(domains.items()):
        print(f"\n[ Domain: {dom} ]")
        for t in sorted(tool_list):
            print(f"  - {t}")

if __name__ == "__main__":
    list_tools()
