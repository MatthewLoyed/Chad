
import sqlite3
import asyncio
import os
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.core.intelligence.session_key_manager import SessionKeyManager

load_dotenv()

async def migrate_telegram_chats():
    db_path = "data/telegram_chats.db"
    if not os.path.exists(db_path):
        print(f"Skipping Telegram chats: {db_path} not found.")
        return

    print("Migrating Telegram chats...")
    supabase = get_supabase_manager()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM telegram_chats")
    rows = cursor.fetchall()
    
    for row in rows:
        data = dict(row)
        # SQLite uses 1/0 for bool, Supabase expects bool
        data['is_active'] = bool(data['is_active'])
        print(f"  Uploading chat {data['chat_id']} (@{data['username']})...")
        await supabase.insert("telegram_chats", data)
    
    conn.close()
    print("Telegram chats migration complete.")

async def migrate_tasks():
    db_path = "data/tasks.db"
    if not os.path.exists(db_path):
        print(f"Skipping Tasks: {db_path} not found.")
        return

    print("Migrating Tasks and Executions...")
    supabase = get_supabase_manager()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Tasks
    cursor.execute("SELECT * FROM agent_tasks")
    tasks = cursor.fetchall()
    for row in tasks:
        data = dict(row)
        if 'id' in data: del data['id'] # Let Supabase handle bigserial
        if data['metadata']: data['metadata'] = json.loads(data['metadata'])
        print(f"  Uploading task {data['task_id']} ({data['name']})...")
        await supabase.insert("agent_tasks", data)
    
    # Executions
    cursor.execute("SELECT * FROM task_executions")
    execs = cursor.fetchall()
    for row in execs:
        data = dict(row)
        if 'id' in data: del data['id']
        data['success'] = bool(data['success'])
        if data['args']: data['args'] = json.loads(data['args'])
        if data['result']: data['result'] = json.loads(data['result'])
        print(f"  Uploading execution for task {data['task_id']}...")
        await supabase.insert("task_executions", data)
        
    conn.close()
    print("Tasks migration complete.")

async def migrate_memory():
    """Migrates all memory databases found in data/profiles/ or at the root."""
    supabase = get_supabase_manager()
    
    # Potential locations
    possible_paths = [
        Path("data/memory.db"),
        Path("data/profiles/default/memory.db")
    ]
    
    # Auto-discover other profiles
    profiles_dir = Path("data/profiles")
    if profiles_dir.exists():
        for profile_dir in profiles_dir.iterdir():
            if profile_dir.is_dir():
                mem_db = profile_dir / "memory.db"
                if mem_db.exists() and mem_db not in possible_paths:
                    possible_paths.append(mem_db)

    found_any = False
    for db_path in possible_paths:
        if not db_path.exists():
            continue
            
        found_any = True
        print(f"Migrating Memories and History from {db_path}...")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Memories
        try:
            cursor.execute("SELECT * FROM memories")
            rows = cursor.fetchall()
            for row in rows:
                data = dict(row)
                if 'id' in data: del data['id']
                if data.get('embedding'): 
                    try:
                        data['embedding'] = json.loads(data['embedding'])
                    except:
                        pass # Already a list or invalid
                print(f"  Uploading memory: {data['content'][:30]}...")
                await supabase.insert("memories", data)
        except Exception as e:
            print(f"  Error migrating memories from {db_path}: {e}")
            
        # History
        try:
            cursor.execute("SELECT * FROM conversation_history")
            rows = cursor.fetchall()
            for row in rows:
                data = dict(row)
                if 'id' in data: del data['id']
                if data.get('metadata'):
                    try:
                        data['metadata'] = json.loads(data['metadata'])
                    except:
                        pass
                print(f"  Uploading history message: {data['role']} - {data['content'][:30]}...")
                await supabase.insert("conversation_history", data)
        except Exception as e:
            print(f"  Error migrating history from {db_path}: {e}")
            
        conn.close()

    if not found_any:
        print("No memory databases found to migrate.")
    else:
        print("Memory migration complete.")

async def main():
    print("Starting Supabase Migration...")
    await migrate_telegram_chats()
    await migrate_tasks()
    await migrate_memory()
    print("\nALL MIGRATIONS COMPLETED.")

if __name__ == "__main__":
    asyncio.run(main())
