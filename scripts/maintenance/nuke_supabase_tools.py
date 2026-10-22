
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def nuke_tools():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    base_url = f"{url}/rest/v1/tools"
    
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    
    print(f"Nuking all tools in {base_url}...")
    async with httpx.AsyncClient() as client:
        # Delete all rows (no filters means all rows in PostgREST delete)
        # Note: We need a filter to prevent accidental full table delete if not configured,
        # but here we WANT a full table delete. PostgREST requires a filter usually unless allowed.
        # We'll use name=neq.EMPTY_STRING
        resp = await client.delete(f"{base_url}?name=neq.", headers=headers)
        if resp.status_code in (200, 204):
            print("Successfully nuked all tools.")
        else:
            print(f"Failed to nuke: {resp.status_code} - {resp.text}")

if __name__ == "__main__":
    asyncio.run(nuke_tools())
