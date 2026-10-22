"""
[TEST] Verification Script: Hands OS Daemon Bridge
Spawns an in-process Starlette mockup, connects the HandsDaemon,
authenticates with token verification, and runs full-duplex RPC primitives.
"""

import sys
import os
import asyncio
import uvicorn
import json
from pathlib import Path
from threading import Thread

# Append project root to PYTHONPATH
sys.path.append(str(Path(__file__).parent.parent.parent))

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket

# Import our freshly designed Decoupling Architecture components
from src.remote.server import _StarletteWSAdapter
from src.remote.daemon_bridge import DaemonExecutorBridge
from src.daemons.hands import HandsDaemon
from src.daemons.protocol import DaemonAction

# Configure testing credentials
TEST_PORT = 8799
# Read the actual token that was loaded by system modules (.env) to avoid override collisions!
TEST_TOKEN = os.getenv("CHAD_SERVER_TOKEN", "dummy_verification_token")

print(f"[ CONFIG ] Resolved testing auth token: {TEST_TOKEN[:6]}...{TEST_TOKEN[-6:]}")

print("\n==================================================")
print("[+] STARTING HANDS DAEMON VERIFICATION PIPELINE")
print("==================================================\n")

# 1. Create a Mock Server Side handler using the exact DaemonBridge logic
async def mock_daemon_handler(ws: WebSocket):
    await ws.accept()
    adapter = _StarletteWSAdapter(ws)
    bridge = DaemonExecutorBridge()
    
    print("[ SERVER ] Connection upgrade received. Challenging client...")
    
    try:
        # Auth Handshake
        async for auth_str in adapter:
            payload = json.loads(auth_str)
            print(f"[ SERVER DEBUG ] Recv payload: {payload} (Expected token: '{TEST_TOKEN}')")
            if payload.get("type") == "auth" and payload.get("auth_token") == TEST_TOKEN:
                print("[ SERVER ] CLIENT AUTHENTICATED!")
                await adapter.send(json.dumps({"status": "authenticated"}))
                bridge.register_daemon(adapter)
                break
            else:
                print("[ SERVER ] CLIENT REJECTED!")
                await adapter.close()
                return

        # Event frame parsing
        async for resp_str in adapter:
            resp_dict = json.loads(resp_str)
            print(f"[ SERVER ] Received RPC Frame: {resp_str[:100]}...")
            await bridge.handle_daemon_response(resp_dict)
            
    except Exception as e:
        print(f"[ SERVER ERR ] Handler encountered error: {e}")
    finally:
        bridge.unregister_daemon(adapter)

# 2. Define Lightweight Starlette Application
app = Starlette(routes=[
    WebSocketRoute("/ws/daemon", mock_daemon_handler)
])

# Thread runner to keep Uvicorn from blocking main async event loop
def start_uvicorn_server():
    uvicorn.run(app, host="127.0.0.1", port=TEST_PORT, log_level="warning")

async def run_test_suite():
    print("[ STEP 1 ] Spawning In-Memory Starlette server on port", TEST_PORT)
    server_thread = Thread(target=start_uvicorn_server, daemon=True)
    server_thread.start()
    await asyncio.sleep(1.5) # Warm-up
    
    print("\n[ STEP 2 ] Launching HandsDaemon in-process background task...")
    daemon = HandsDaemon(server_url=f"ws://127.0.0.1:{TEST_PORT}/ws/daemon")
    daemon_task = asyncio.create_task(daemon.start())
    
    print("[ TEST ] Waiting for bridge authorization...")
    bridge = DaemonExecutorBridge()
    
    # Poll for registration up to 5 seconds
    for _ in range(50):
        if bridge.is_ready:
            break
        await asyncio.sleep(0.1)
        
    assert bridge.is_ready, "FAIL: HandsDaemon failed to link within 5 seconds!"
    print("\n[+] [ OK ] Daemon and Server fully linked and ready for traffic!")
    
    # -------------------------------------------------------------------------
    # RPC Test Case A: Heartbeat PING
    # -------------------------------------------------------------------------
    print("\n[ TEST A ] Executing Ping Command across RPC...")
    ping_res = await bridge.execute_primitive(action=DaemonAction.PING, payload={}, timeout=3.0)
    print(f"--> Result: {ping_res}")
    assert ping_res == "pong", "FAIL: Expected pong response!"
    print("[ OK ] Test A (Network Heartbeat) passed!")

    # -------------------------------------------------------------------------
    # RPC Test Case B: Non-destructive Local Action (Window Enumeration Mock/Empty)
    # We will run a simple 'resize_window' query targeting a fake window and expect failure
    # -------------------------------------------------------------------------
    print("\n[ TEST B ] Executing Faulty Action (Non-existent Window) to test error propagation...")
    try:
        await bridge.execute_primitive(
            action=DaemonAction.RESIZE_WINDOW,
            payload={"window_title": "FAKE_TEST_WINDOW_ZZYXX_99999", "width": 800, "height": 600},
            timeout=3.0
        )
        print("--> FAIL: This should have raised an Exception!")
        assert False, "Should fail"
    except Exception as e:
        print(f"--> Correctly Caught Remote Error: {e}")
        assert "not found" in str(e).lower()
        print("[ OK ] Test B (Exception Routing) passed!")

    # -------------------------------------------------------------------------
    # RPC Test Case C: Parallel Execution
    # -------------------------------------------------------------------------
    print("\n[ TEST C ] Stress Testing with Concurrent RPC Commands...")
    # Send 3 pings simultaneously
    t1 = bridge.execute_primitive(DaemonAction.PING, {})
    t2 = bridge.execute_primitive(DaemonAction.PING, {})
    t3 = bridge.execute_primitive(DaemonAction.PING, {})
    results = await asyncio.gather(t1, t2, t3)
    print(f"--> Concurrent Results: {results}")
    assert all(r == "pong" for r in results)
    print("[ OK ] Test C (Parallel Multiplexing) passed!")
    
    # Clean shutdown
    print("\n==================================================")
    print("[ SUCCESS ] VERIFICATION COMPLETE: ALL TESTS PASSED!")
    print("==================================================")
    
    daemon._running = False
    daemon_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(run_test_suite())
    except KeyboardInterrupt:
        print("\nManual interrupt. Closing down...")
    sys.exit(0)
