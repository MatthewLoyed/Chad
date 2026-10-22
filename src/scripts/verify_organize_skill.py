
import asyncio
import sys
import os

# Add project root and agent assets to path
project_root = os.getcwd()
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "src", "skills"))

from src.tools.os.path_resolver_tool import PathResolverTool
from src.skills import OrganizeFolderSkill

async def test():
    print("--- 1. Testing Path Resolver ---")
    resolver = PathResolverTool()
    
    cases = ["Downloads", "Desktop", "~/Documents", ".", "C:/Windows"]
    for case in cases:
        result = await resolver.execute(case)
        print(f"'{case}' -> {result.get('resolved_path')} (Success: {result.get('success')})")

    print("\n--- 2. Testing Organize Skill (Dry Run) ---")
    skill = OrganizeFolderSkill(config={})
    
    # Scout the actual Downloads folder (Safe, it only scouts)
    # We pass a known safe path to avoid accidentally organizing something massive during a test
    # But since it's shadow mode, it should be fine.
    
    target = "Downloads"
    print(f"Invoking skill on: {target}")
    result = await skill.execute(target)
    
    print("Skill Result Keys:", result.keys())
    if result.get("success"):
        print("Moves Proposed:", len(result.get("moves", [])))
        # Use a safer way to print potentially emoji-rich text to Windows terminal
        try:
            print("Sample Proposal:\n", result.get("result")[:500])
        except UnicodeEncodeError:
            print("Sample Proposal:\n", result.get("result")[:500].encode('ascii', 'replace').decode())
    else:
        print("Error:", result.get("error"))

if __name__ == "__main__":
    asyncio.run(test())

