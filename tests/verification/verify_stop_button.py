"""
Automated Verification Script for Stop Button Interruption Logic.
Verifies that publishing TaskStoppedByUser cancels the active task in LimbCoordinator,
runs cleanup, and transitions state back to ready.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.application.events import EventBus, TaskStoppedByUser
from src.remote.limb_coordinator import LimbCoordinator
from src.remote.protocol import ChadMessage, MessageType


class MockWebSocket:
    def __init__(self):
        self.sent_messages = []

    async def send(self, message_str: str):
        self.sent_messages.append(message_str)


async def main():
    print("=" * 60)
    print("Verifying Stop Button Interruption & Cancellation logic...")
    print("=" * 60)

    # 1. Initialize EventBus and LimbCoordinator
    event_bus = EventBus()
    init_ready = asyncio.Event()
    init_ready.set()
    
    coordinator = LimbCoordinator(event_bus, init_ready)
    coordinator.setup_event_subscriptions()

    # 2. Setup mock WebSocket client
    ws_mock = MockWebSocket()
    coordinator.register_active_websocket(ws_mock)

    # 3. Setup mock Turn Coordinator / Session Manager / Orchestrator
    orchestrator_mock = AsyncMock()
    # Simulate a long running task (e.g. LLM generation)
    async def slow_process(*args, **kwargs):
        print("[ MOCK ORCHESTRATOR ] process_request started...")
        try:
            await asyncio.sleep(10.0)
            return "Generation complete."
        except asyncio.CancelledError:
            print("[ MOCK ORCHESTRATOR ] process_request task cancelled!")
            raise

    orchestrator_mock.process_request = slow_process
    orchestrator_mock.cancel_turn = AsyncMock()

    session_manager_mock = MagicMock()
    session_manager_mock.conversation_orchestrator = orchestrator_mock

    turn_coordinator_mock = MagicMock()
    turn_coordinator_mock.session_manager = session_manager_mock
    
    coordinator.bind_components(turn_coordinator_mock)

    # 4. Dispatch a USER_INPUT message to start the task
    user_msg = ChadMessage(MessageType.USER_INPUT, {"text": "What is the meaning of life?"})
    print("\n[TEST] Simulating incoming user message...")
    await coordinator.handle_incoming_message(user_msg, ws_mock)

    # Give it a millisecond to spawn the task
    await asyncio.sleep(0.01)

    # Verify the task has started
    assert coordinator.active_tasks.get("gui") is not None
    assert not coordinator.active_tasks["gui"].done()
    print("  [PASS] Active task spawned and running in background.")

    # 5. Trigger Stop Button Interruption Event (publish TaskStoppedByUser to EventBus)
    print("\n[TEST] Simulating Stop button clicked (publishing TaskStoppedByUser)...")
    event_bus.publish(TaskStoppedByUser())

    # Wait for task to finish cancellation and cleanup
    await asyncio.sleep(0.1)

    # Verify the task has been cancelled/completed
    assert coordinator.active_tasks["gui"].done()
    print("  [PASS] Active task has been stopped/cancelled successfully.")

    # Verify cancel_turn was called on the orchestrator
    orchestrator_mock.cancel_turn.assert_called_once_with(reason="user_requested")
    print("  [PASS] cancel_turn was called on conversation orchestrator.")

    # Verify status_update ready broadcast was sent to websocket
    status_updates = [
        msg for msg in ws_mock.sent_messages 
        if "status_update" in msg and "ready" in msg
    ]
    assert len(status_updates) > 0
    print("  [PASS] Ready status broadcast was sent to client UI.")

    print("\n" + "=" * 60)
    print("[ SUCCESS ] All stop button verification checks passed!")
    print("=" * 60)


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
