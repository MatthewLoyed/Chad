
import httpx
import os
import asyncio
from dotenv import load_dotenv

async def probe_supabase():
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    
    if not url or not key:
        print("Missing Supabase credentials in .env")
        return

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}"
    }

    async with httpx.AsyncClient() as client:
        # Try to list tables or just check 'tools'
        print(f"Probing {url}/rest/v1/tools...")
        try:
            # Get all column names for 'tools'
            response = await client.request("GET", f"{url}/rest/v1/tools?select=*&limit=1", headers=headers)
            if response.status_code == 200:
                print("SUCCESS: Found 'tools' table.")
                data = response.json()[0]
                print(f"Columns: {list(data.keys())}")
                print(f"Sample data: {data}")
            else:
                print(f"FAILED: Status {response.status_code}, Body: {response.text}")

            # Try to check for other tables
            for table in ["telegram_chats", "agent_tasks", "memories"]:
                res = await client.request("GET", f"{url}/rest/v1/{table}?limit=1", headers=headers)
                if res.status_code == 200:
                    print(f"SUCCESS: Found '{table}' table.")
                elif res.status_code == 404:
                    print(f"NOT FOUND: '{table}' table.")
                else:
                    print(f"INFO: '{table}' access status {res.status_code}")

        except Exception as e:
            print(f"Error during probe: {e}")

if __name__ == "__main__":
    asyncio.run(probe_supabase())
