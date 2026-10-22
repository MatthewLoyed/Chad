import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.infrastructure.repositories.memory_repository import MemoryRepository

def migrate_to_cloud():
    repo = MemoryRepository()
    chad_dir = Path("chad")
    
    if not chad_dir.exists():
        print(f"Error: {chad_dir} directory not found.")
        return

    files_to_sync = ["AGENT.md", "SOUL.md", "USER.md", "IDENTITY.md", "TOOLS.md", "HEARTBEAT.md"]
    
    print(f"Starting persona migration to Supabase...")
    
    for filename in files_to_sync:
        filepath = chad_dir / filename
        if filepath.exists():
            print(f"  Uploading {filename}...")
            content = filepath.read_text(encoding="utf-8")
            # Normalize ID to lowercase without extension (e.g. 'agent')
            config_id = filename.split('.')[0].lower()
            
            success = repo.update_persona_config(config_id, content)
            if success:
                print(f"  [OK] {filename} synced to 'persona_config' table.")
            else:
                print(f"  [FAIL] Failed to sync {filename}.")
        else:
            print(f"  [SKIP] Skipping {filename} (not found locally).")

    print("\nMigration complete. Supabase is now the Master Source for your Persona.")

if __name__ == "__main__":
    migrate_to_cloud()
