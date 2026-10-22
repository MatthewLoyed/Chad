import sys
import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.shared.config_manager import ConfigManager

def inspect_memories():
    ConfigManager.load_config()
    db_path = ConfigManager.get_memory_db_path()
    print(f"DB Path: {db_path}")
    
    if not os.path.exists(db_path):
        print("❌ Error: DB file does not exist.")
        return
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get list of tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables in DB: {tables}")
    
    for table_tuple in tables:
        table_name = table_tuple[0]
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            print(f"\nTable: {table_name} - Total records: {count}")
            
            if count > 0:
                cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
                columns = [desc[0] for desc in cursor.description]
                print(f"Columns: {columns}")
                rows = cursor.fetchall()
                for r in rows:
                    print(f"  {r[:5]} ...")
        except Exception as e:
            print(f"  Failed to inspect {table_name}: {e}")
            
    conn.close()

if __name__ == "__main__":
    inspect_memories()
