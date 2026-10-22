import os
import asyncio
import psycopg
from dotenv import load_dotenv

# Load .env
load_dotenv()

async def test_db():
    db_url = os.getenv("SUPABASE_DB_URL")
    print(f"Connecting to: {db_url}")
    
    try:
        # Try a direct connection with a short timeout
        conn = await psycopg.AsyncConnection.connect(db_url, connect_timeout=5)
        print("✅ Connection successful!")
        
        async with conn.cursor() as cur:
            await cur.execute("SELECT version();")
            version = await cur.fetchone()
            print(f"Database version: {version[0]}")
            
        await conn.close()
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        
        # If pooler fails, try direct host if we can resolve it
        if "pooler" in db_url:
            print("\nAttempting to resolve direct host...")
            project_ref = db_url.split("@")[0].split(".")[-1]
            if ":" in project_ref:
                project_ref = project_ref.split(":")[0]
            
            # Reconstruct direct URL
            # postgres://postgres.[REF]:[PASS]@db.[REF].supabase.co:5432/postgres
            direct_host = f"db.sdkidlgqagwoletstmcr.supabase.co"
            print(f"Targeting direct host: {direct_host}")
            
            # Extract password from current URL
            import re
            match = re.search(r":([^@]+)@", db_url)
            password = match.group(1) if match else "UNKNOWN"
            
            direct_url = f"postgresql://postgres.sdkidlgqagwoletstmcr:{password}@{direct_host}:5432/postgres"
            
            try:
                conn = await psycopg.AsyncConnection.connect(direct_url, connect_timeout=5)
                print("✅ Direct connection successful!")
                await conn.close()
            except Exception as e2:
                print(f"❌ Direct connection also failed: {e2}")

if __name__ == "__main__":
    import selectors
    # Windows fix: Psycopg requires SelectorEventLoop for async
    if os.name == 'nt':
        loop_factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    else:
        loop_factory = None
        
    asyncio.run(test_db(), loop_factory=loop_factory)
