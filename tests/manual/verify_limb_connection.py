"""
Test: Verify Limb can connect to Brain (no GUI, just connection test)
Usage: python tests/manual/test_limb_connection.py

This test verifies:
1. WebSocket connection to the server
2. Authentication handshake
3. Basic message round-trip

Set environment variables:
- CHAD_SERVER_URL (default: ws://localhost:8767/ws)
- CHAD_REMOTE_TOKEN (default: chad-dev-secret)
"""
import asyncio
import os
import sys
import logging
from datetime import datetime

# Ensure project root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.remote.limb import ChadLimb
from src.shared.logger import setup_logger

logger = setup_logger("TestLimbConnection")
logger.setLevel(logging.INFO)

class TestResult:
    def __init__(self):
        self.connected = False
        self.authenticated = False
        self.received_message = False
        self.error = None
        self.start_time = datetime.now()

async def test_connection(timeout_seconds: int = 30):
    """Test the limb connection with detailed status reporting."""
    result = TestResult()
    url = os.getenv("CHAD_SERVER_URL", "ws://localhost:8767/ws")
    token = os.getenv("CHAD_REMOTE_TOKEN", "chad-dev-secret")

    print("\n" + "="*60)
    print("CHAD LIMB CONNECTION TEST")
    print("="*60)
    print(f"Server URL: {url}")
    print(f"Token: {token[:10]}...")
    print(f"Timeout: {timeout_seconds}s")
    print("="*60 + "\n")

    try:
        limb = ChadLimb(server_url=url)

        # Track connection status
        async def monitor_connection():
            await asyncio.sleep(2)
            if limb.websocket:
                result.connected = True
                print("[OK] WebSocket connected")
            else:
                print("[FAIL] WebSocket failed to connect")
                result.error = "Connection failed"
                limb.is_running = False
                return

            # Wait a bit for auth
            await asyncio.sleep(3)
            if result.authenticated:
                print("[OK] Authentication successful")
            else:
                print("[FAIL] Authentication failed or timed out")
                result.error = "Auth timeout"

            # Wait for the full timeout
            await asyncio.sleep(timeout_seconds - 5)
            limb.is_running = False

        # Override handle_message to track auth
        original_handle = limb.handle_message
        async def tracked_handle(msg):
            from src.remote.protocol import MessageType
            if msg.type == MessageType.AUTH.value:
                if msg.payload.get("status") == "success":
                    result.authenticated = True
            elif msg.type == MessageType.AI_RESPONSE.value:
                result.received_message = True
                print(f"[OK] Received AI response: {msg.payload.get('text', '')[:50]}...")
            await original_handle(msg)

        limb.handle_message = tracked_handle

        # Run the connection test
        await asyncio.gather(
            limb.connect(),
            monitor_connection()
        )

    except Exception as e:
        result.error = str(e)
        print(f"[FAIL] Exception: {e}")
        logger.error(f"Test failed with exception", exc_info=True)

    # Print results
    elapsed = (datetime.now() - result.start_time).total_seconds()
    print("\n" + "="*60)
    print("TEST RESULTS")
    print("="*60)
    print(f"Duration: {elapsed:.1f}s")
    print(f"Connected: {'[OK] YES' if result.connected else '[FAIL] NO'}")
    print(f"Authenticated: {'[OK] YES' if result.authenticated else '[FAIL] NO'}")
    print(f"Message received: {'[OK] YES' if result.received_message else '[FAIL] NO'}")
    if result.error:
        print(f"Error: {result.error}")
    print("="*60 + "\n")

    # Return exit code
    if result.connected and result.authenticated:
        print("[OK] TEST PASSED: Connection successful")
        return 0
    else:
        print("[FAIL] TEST FAILED: Connection issues detected")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(test_connection())
    sys.exit(exit_code)
