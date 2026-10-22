import os
import sys
import asyncio
import logging

# Ensure src is on PYTHONPATH
sys.path.append(os.getcwd())

from src.clients.desktop.api_bridge import RemoteCommandHandler
from src.application.commands.chat_history_commands import GetConversationsCommand
from src.shared.config_manager import ConfigManager

async def test_stateless_relay():
    print("\n" + "="*60)
    print("TESTING STATELESS COMMAND RELAY TO BRAIN SERVER")
    print("="*60 + "\n")
    
    # 1. Resolve local configuration
    server_url = sys.argv[1] if len(sys.argv) > 1 else (os.getenv("CHAD_SERVER_URL") or ConfigManager.get("chad.server_url", "ws://localhost:8767"))
    
    # Ensure we convert WebSocket to HTTP for REST client
    base_url = server_url.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    if "/ws" in base_url:
        base_url = base_url.replace("/ws", "")
        
    token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
    
    print(f"[ CONFIG ] Server URL: {server_url}")
    print(f"[ CONFIG ] Relay Target: {base_url}/api/command")
    print(f"[ CONFIG ] Secure Auth Token resolved: {'YES' if token else 'NO'}\n")
    
    # 2. Construct the command handler
    handler = RemoteCommandHandler(server_url, token)
    
    # 3. Dispatch GetConversationsCommand
    command = GetConversationsCommand(limit=5, offset=0)
    print(f"[ ACTION ] Relaying GetConversationsCommand to Brain...")
    
    try:
        result = await handler.execute(command)
        print(f"[ SUCCESS ] Received response from Brain!")
        
        # Validate shape
        if isinstance(result, list):
            print(f"[ DATA ] Response type is correct: LIST. Count: {len(result)}")
            for i, conv in enumerate(result):
                title = conv.get('title', 'Untitled')
                cid = conv.get('id', 'N/A')
                updated = conv.get('updated_at', 'N/A')
                print(f"   -> Conversation #{i+1}: {title} (ID: {cid}, Updated: {updated})")
            
            if len(result) == 0:
                print("   (Empty conversation history on server, valid state)")
            
            print("\n" + "="*60)
            print("RELAY VERIFICATION COMPLETED SUCCESSFULLY!")
            print("Stateless HTTP commands are flowing seamlessly between Eyes & Brain.")
            print("="*60 + "\n")
            return True
        else:
            print(f"[ FAILURE ] Server returned invalid format. Expected LIST, but got {type(result)}: {result}")
            return False
            
    except Exception as err:
        print(f"[ ERROR ] Relay command execution failed: {err}")
        print("  -> If error is 503 Service Unavailable, please wait a moment for server startup and rerun.")
        print("\n" + "="*60)
        print("RELAY VERIFICATION ENCOUNTERED ERRORS")
        print("="*60 + "\n")
        return False

if __name__ == "__main__":
    # Configure basic logging to see what's happening
    logging.basicConfig(level=logging.INFO)
    # Avoid noisy third-party logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    asyncio.run(test_stateless_relay())
