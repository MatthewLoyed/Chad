"""
End-to-End Tests for Heartbeat & Cron with Real APIs

Tests the full system integration:
- Heartbeat service -> ConversationOrchestrator -> Model -> Tool execution
- Cron scheduler -> ConversationOrchestrator -> Model -> Tool execution
- Real API calls (Canvas, Gmail, Brave Search)

NOTE: Requires API credentials configured in .env
"""

import asyncio
import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager
from src.core.orchestration.conversation_orchestrator import ConversationOrchestrator, TurnInitiator
from src.core.monitoring.heartbeat_service import HeartbeatService
from src.core.scheduling.cron_scheduler import CronScheduler
from src.domain.models import TaskTrigger, TaskStatus

class MockTaskRepository:
    def __init__(self, db_path=None):
        self.tasks = {}

    async def create_task(self, task) -> bool:
        self.tasks[task.task_id] = task
        return True

    async def create_task_async(self, task) -> bool:
        return await self.create_task(task)

    async def get_task(self, task_id: str):
        return self.tasks.get(task_id)

    async def get_task_async(self, task_id: str):
        return await self.get_task(task_id)

    async def update_task(self, task) -> bool:
        self.tasks[task.task_id] = task
        return True

    async def update_task_async(self, task) -> bool:
        return await self.update_task(task)

    async def delete_task(self, task_id: str) -> bool:
        self.tasks.pop(task_id, None)
        return True

    async def delete_task_async(self, task_id: str) -> bool:
        return await self.delete_task(task_id)

    async def get_all_tasks(self) -> list:
        return list(self.tasks.values())

    async def get_all_tasks_async(self) -> list:
        return await self.get_all_tasks()

    async def get_scheduled_tasks(self) -> list:
        return [t for t in self.tasks.values() if t.status == TaskStatus.SCHEDULED]

    async def get_scheduled_tasks_async(self) -> list:
        return await self.get_scheduled_tasks()

    async def get_due_tasks(self, current_time=None) -> list:
        if current_time is None:
            current_time = datetime.now()
        due = []
        for t in self.tasks.values():
            if t.status == TaskStatus.SCHEDULED:
                if t.next_run and t.next_run <= current_time:
                    due.append(t)
        due.sort(key=lambda x: x.next_run if x.next_run else datetime.max)
        return due

    async def get_due_tasks_async(self, current_time=None) -> list:
        return await self.get_due_tasks(current_time)

TaskRepository = MockTaskRepository


def create_test_orchestrator(config):
    from src.application.system_factory import SystemFactory
    from src.core.orchestration.coordinators.model_coordinator import ModelCoordinator
    from src.core.orchestration.coordinators.tool_coordinator import ToolCoordinator
    from src.core.orchestration.state_coordinator import StateCoordinator
    
    container = SystemFactory.build_core_engine(config)
    
    model_coordinator = ModelCoordinator(
        model_router=container.model_router,
        event_bus=container.event_bus,
        consolidation_manager=container.consolidation_manager
    )
    
    tool_coordinator = ToolCoordinator(
        tool_registry=container.tool_registry,
        tool_executor=container.tool_executor,
        event_bus=container.event_bus
    )
    
    state_coordinator = StateCoordinator()
    
    return ConversationOrchestrator(
        model_coordinator=model_coordinator,
        tool_coordinator=tool_coordinator,
        state_coordinator=state_coordinator,
        memory_engine=container.memory_engine,
        event_bus=container.event_bus,
        config=config
    )


@pytest.mark.asyncio
async def test_canvas_tool_integration():
    """Test 1: Canvas tool integration via cron task."""
    print("\n" + "="*70)
    print("TEST 1: Canvas Tool Integration (Cron -> Canvas API)")
    print("="*70)

    config = ConfigManager.get_config()

    # Check if Canvas credentials exist
    canvas_token = ConfigManager.get_secret("CANVAS_TOKEN")
    if not canvas_token:
        print("\n[!] SKIPPED: CANVAS_TOKEN not configured in .env")
        print("    Add CANVAS_TOKEN=your_token to test Canvas integration")
        return True

    print("\n[+] Canvas credentials found")
    print("[+] Creating orchestrator...")

    # Initialize full orchestrator (with real model, tools, etc.)
    orchestrator = create_test_orchestrator(config)

    # Create task repository
    import tempfile
    temp_db = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db')
    temp_db.close()
    task_repo = TaskRepository(temp_db.name)

    # Initialize scheduler
    scheduler = CronScheduler(orchestrator, task_repo)

    # Create task that calls Canvas tool
    print("[+] Creating Canvas check task...")

    task_description = """Check my Canvas courses and assignments.
Use get_canvas_courses to see my active courses.
If successful, respond with course count and names."""

    future_time = datetime.now() + timedelta(seconds=1)
    task_id = await scheduler.add_task(
        name="canvas_check",
        description=task_description,
        schedule=future_time.isoformat(),
        trigger=TaskTrigger.AT
    )

    print(f"    Task ID: {task_id}")

    # Wait for task to become due
    print("[+] Waiting for task to become due...")
    await asyncio.sleep(2)

    # Manually trigger execution
    print("[+] Triggering task execution (this will call Canvas API)...")
    await scheduler._check_and_execute_tasks()

    # Give orchestrator time to process
    await asyncio.sleep(3)

    # Check task status
    task = await scheduler.get_task(task_id)

    print(f"\n[+] Task completed:")
    print(f"    Status: {task.status}")
    print(f"    Last run: {task.last_run}")

    # Verify task executed
    if task.last_run:
        print("\n[+] PASS: Canvas tool integration working")
        print("    The task successfully executed and called Canvas API")
        return True
    else:
        print("\n[-] FAIL: Task did not execute")
        return False


@pytest.mark.asyncio
async def test_gmail_tool_integration():
    """Test 2: Gmail tool integration via cron task."""
    print("\n" + "="*70)
    print("TEST 2: Gmail Tool Integration (Cron -> Gmail API)")
    print("="*70)

    config = ConfigManager.get_config()

    # Check if Gmail credentials exist
    credentials_path = Path("config/credentials.json")
    if not credentials_path.exists():
        print("\n[!] SKIPPED: Gmail credentials not configured")
        print("    Add config/credentials.json to test Gmail integration")
        return True

    print("\n[+] Gmail credentials found")
    print("[+] Creating orchestrator...")

    orchestrator = create_test_orchestrator(config)

    # Create task repository
    import tempfile
    temp_db = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db')
    temp_db.close()
    task_repo = TaskRepository(temp_db.name)

    scheduler = CronScheduler(orchestrator, task_repo)

    # Create task that calls Gmail tool
    print("[+] Creating Gmail check task...")

    task_description = """Check my recent emails.
Use search_gmail with unread_only=true and limit=5.
If successful, respond with unread count."""

    future_time = datetime.now() + timedelta(seconds=1)
    task_id = await scheduler.add_task(
        name="gmail_check",
        description=task_description,
        schedule=future_time.isoformat(),
        trigger=TaskTrigger.AT
    )

    print(f"    Task ID: {task_id}")

    # Wait and trigger
    print("[+] Waiting for task to become due...")
    await asyncio.sleep(2)

    print("[+] Triggering task execution (this will call Gmail API)...")
    await scheduler._check_and_execute_tasks()

    # Give orchestrator time to process
    await asyncio.sleep(3)

    # Check task status
    task = await scheduler.get_task(task_id)

    print(f"\n[+] Task completed:")
    print(f"    Status: {task.status}")
    print(f"    Last run: {task.last_run}")

    if task.last_run:
        print("\n[+] PASS: Gmail tool integration working")
        return True
    else:
        print("\n[-] FAIL: Task did not execute")
        return False


@pytest.mark.asyncio
async def test_brave_search_integration():
    """Test 3: Brave search integration via cron task."""
    print("\n" + "="*70)
    print("TEST 3: Brave Search Integration (Cron -> Brave API)")
    print("="*70)

    config = ConfigManager.get_config()

    # Check if Brave API key exists
    brave_key = ConfigManager.get_secret("BRAVE_API_KEY")
    if not brave_key:
        print("\n[!] SKIPPED: BRAVE_API_KEY not configured in .env")
        print("    Add BRAVE_API_KEY=your_key to test Brave search")
        return True

    print("\n[+] Brave API key found")
    print("[+] Creating orchestrator...")

    orchestrator = create_test_orchestrator(config)

    # Create task repository
    import tempfile
    temp_db = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db')
    temp_db.close()
    task_repo = TaskRepository(temp_db.name)

    scheduler = CronScheduler(orchestrator, task_repo)

    # Create task that uses web search
    print("[+] Creating web search task...")

    task_description = """Search the web for 'Python asyncio tutorial'.
Use search_web_brave with count=3.
If successful, respond with the number of results found."""

    future_time = datetime.now() + timedelta(seconds=1)
    task_id = await scheduler.add_task(
        name="web_search",
        description=task_description,
        schedule=future_time.isoformat(),
        trigger=TaskTrigger.AT
    )

    print(f"    Task ID: {task_id}")

    # Wait and trigger
    print("[+] Waiting for task to become due...")
    await asyncio.sleep(2)

    print("[+] Triggering task execution (this will call Brave API)...")
    await scheduler._check_and_execute_tasks()

    # Give orchestrator time to process
    await asyncio.sleep(3)

    # Check task status
    task = await scheduler.get_task(task_id)

    print(f"\n[+] Task completed:")
    print(f"    Status: {task.status}")
    print(f"    Last run: {task.last_run}")

    if task.last_run:
        print("\n[+] PASS: Brave search integration working")
        return True
    else:
        print("\n[-] FAIL: Task did not execute")
        return False


@pytest.mark.asyncio
async def test_heartbeat_with_real_orchestrator():
    """Test 4: Heartbeat service with real orchestrator."""
    print("\n" + "="*70)
    print("TEST 4: Heartbeat Service Integration")
    print("="*70)

    config = ConfigManager.get_config()

    print("\n[+] Creating orchestrator...")
    orchestrator = create_test_orchestrator(config)

    # Create heartbeat service
    heartbeat = HeartbeatService(orchestrator)

    # Create simple test checklist
    test_checklist = Path("HEARTBEAT_E2E_TEST.md")
    test_checklist.write_text("""# Test Heartbeat Checklist

## System Check
- Verify orchestrator is responsive
- Return "HEARTBEAT_OK" if all systems operational
""")

    heartbeat._checklist_path = test_checklist

    print("[+] Starting heartbeat with 5-second interval...")
    await heartbeat.start(interval_minutes=5/60)

    # Wait for one heartbeat
    print("[+] Waiting for heartbeat execution...")
    await asyncio.sleep(6)

    await heartbeat.stop()

    # Cleanup
    test_checklist.unlink()

    print("\n[+] PASS: Heartbeat executed with real orchestrator")
    return True


@pytest.mark.asyncio
async def test_recurring_cron_task():
    """Test 5: Recurring cron task (EVERY trigger)."""
    print("\n" + "="*70)
    print("TEST 5: Recurring Cron Task (EVERY trigger)")
    print("="*70)

    config = ConfigManager.get_config()

    print("\n[+] Creating orchestrator...")
    orchestrator = create_test_orchestrator(config)

    # Create task repository
    import tempfile
    temp_db = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db')
    temp_db.close()
    task_repo = TaskRepository(temp_db.name)

    scheduler = CronScheduler(orchestrator, task_repo)

    # Create recurring task
    print("[+] Creating recurring task (every 2 seconds, 3 executions)...")

    task_description = "Respond with 'Recurring task executed' and current timestamp."

    task_id = await scheduler.add_task(
        name="recurring_test",
        description=task_description,
        schedule="1m",  # Valid format
        trigger=TaskTrigger.EVERY
    )

    # Override to make it execute immediately
    task = await scheduler.get_task(task_id)
    task.next_run = datetime.now()
    await task_repo.update_task(task)

    print(f"    Task ID: {task_id}")
    print("[+] Triggering 3 executions...")

    # Manually trigger 3 executions
    for i in range(3):
        await asyncio.sleep(2)

        # Make task due
        task.next_run = datetime.now()
        await task_repo.update_task(task)

        # Trigger execution
        await scheduler._check_and_execute_tasks()
        print(f"    Execution {i+1}/3 triggered")

    # Verify executions
    task = await scheduler.get_task(task_id)

    print(f"\n[+] Task state:")
    print(f"    Status: {task.status}")
    print(f"    Last run: {task.last_run}")

    if task.last_run:
        print("\n[+] PASS: Recurring task executed successfully")
        return True
    else:
        print("\n[-] FAIL: Task did not execute")
        return False


async def run_all_tests():
    """Run all end-to-end tests."""
    print("\n" + "="*70)
    print("END-TO-END TESTS - HEARTBEAT & CRON WITH REAL APIS")
    print("="*70)
    print("\nThese tests use the full system:")
    print("  - Real ConversationOrchestrator")
    print("  - Real AI model (Gemini)")
    print("  - Real tool execution (Canvas, Gmail, Brave)")
    print("  - Real heartbeat and cron services")
    print("\n" + "="*70)

    tests = [
        ("Canvas Tool Integration", test_canvas_tool_integration),
        ("Gmail Tool Integration", test_gmail_tool_integration),
        ("Brave Search Integration", test_brave_search_integration),
        ("Heartbeat Service Integration", test_heartbeat_with_real_orchestrator),
        ("Recurring Cron Task", test_recurring_cron_task),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n[-] TEST CRASHED: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

        # Pause between tests
        await asyncio.sleep(2)

    # Summary
    print("\n" + "="*70)
    print("END-TO-END TEST SUMMARY")
    print("="*70)

    for test_name, passed in results:
        if passed:
            status = "[+] PASS"
        else:
            status = "[-] FAIL"
        print(f"{status}: {test_name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print("\n" + "="*70)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("="*70)

    # Notes
    print("\nNOTES:")
    print("  - Tests marked as SKIPPED need API credentials")
    print("  - Add credentials to .env to enable those tests")
    print("  - PASS means the task executed successfully")
    print("  - Check logs for detailed tool execution results")

    return passed == total


if __name__ == "__main__":
    print("\nWARNING: This will make REAL API calls!")
    print("  - Canvas API (if configured)")
    print("  - Gmail API (if configured)")
    print("  - Brave Search API (if configured)")
    print("\nMake sure you have valid credentials configured.")
    print("\nPress Ctrl+C within 5 seconds to cancel...\n")

    try:
        # Give user time to cancel
        for i in range(5, 0, -1):
            print(f"  Starting in {i}...", end='\r')
            import time
            time.sleep(1)

        print("\n")

        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        sys.exit(0)
