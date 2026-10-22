import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

def cleanup_agent_skills():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        print("✗ SUPABASE_URL and SUPABASE_KEY must be set in .env")
        return

    client = create_client(url, key)
    
    # List of skills to remove (those from .agent/skills)
    agent_skills_dirs = [d.name for d in (PROJECT_ROOT / ".agent" / "skills").iterdir() if d.is_dir()]
    
    # Some names might be different in frontmatter, but let's try to find them by path
    # The sync script saves 'file_path' starting with '.agent/skills/'
    
    print(f"[ CLEANUP ] Removing skills with paths starting with '.agent/skills/'...")
    
    try:
        # Fetch all skills to identify them
        response = client.table("skills").select("*").execute()
        to_delete = []
        for skill in response.data:
            if skill.get("file_path", "").startswith(".agent/skills/"):
                to_delete.append(skill["name"])
        
        if not to_delete:
            print("No agent skills found in Supabase.")
            return

        print(f"Found {len(to_delete)} skills to delete: {to_delete}")
        
        for name in to_delete:
            client.table("skills").delete().eq("name", name).execute()
            print(f"  - Deleted: {name}")
            
    except Exception as e:
        print(f"Error during cleanup: {e}")

if __name__ == "__main__":
    cleanup_agent_skills()
