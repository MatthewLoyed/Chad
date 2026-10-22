import asyncio
from pathlib import Path
from src.tools.filesystem.list_directory import ListDirectoryTool

async def main():
    tool = ListDirectoryTool()
    
    print("==========================================================")
    print("1. Listing SMALL folder (config/) at depth=1")
    print("==========================================================")
    res = await tool.execute(path="config", depth=1)
    if res["success"]:
        print(res["result"])
    else:
        print(f"Error: {res['error']}")
        
    print("\n==========================================================")
    print("2. Listing BIG folder (root './') at depth=1 (respecting .gitignore)")
    print("==========================================================")
    res = await tool.execute(path=".", depth=1, respect_gitignore=True)
    if res["success"]:
        print(res["result"])
    else:
        print(f"Error: {res['error']}")

    print("\n==========================================================")
    print("3. Testing TYPO tolerance (asking for './configg/')")
    print("==========================================================")
    res = await tool.execute(path="configg")
    if not res["success"]:
        print(res["error"])
    else:
        print("Expected failure, but succeeded?")

if __name__ == "__main__":
    asyncio.run(main())
