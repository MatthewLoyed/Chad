"""
Simple End-to-End Test - Create Tasks and Verify Execution

This test creates real scheduled tasks in the database and verifies
they execute when the system runs.

Usage:
    1. Run this script to create test tasks
    2. Start Chad normally (python main.py or via GUI)
    3. Wait for tasks to execute
    4. Check logs or task status
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories.task_repository import TaskRepository
from src.domain.models import TaskTrigger, TaskStatus, AgentTask
import uuid


def create_test_tasks():
    """Create test tasks in the database."""
    print("\n" + "="*70)
    print("END-TO-END TEST - CREATE SCHEDULED TASKS")
    print("="*70)

    # Initialize config and repository
    config = ConfigManager()

    # Get task database path
    db_path = config.get("task_db_path", "data/tasks.db")
    print(f"\n[+] Task database: {db_path}")

    task_repo = TaskRepository(db_path)

    print("\n[+] Creating test tasks...")

    tasks_created = []

    # Task 1: Canvas check (if configured)
    canvas_token = config.get_secret("CANVAS_TOKEN")
    if canvas_token:
        # Create task that runs 1 minute from now
        next_run = datetime.now() + timedelta(minutes=1)
        task_id = str(uuid.uuid4())

        task = AgentTask(
            task_id=task_id,
            name="e2e_canvas_check",
            description="Check Canvas courses. Use get_canvas_courses and report course count.",
            trigger=TaskTrigger.AT,
            schedule=next_run.isoformat(),
            status=TaskStatus.SCHEDULED,
            next_run=next_run,
            metadata={"test": True, "e2e": True}
        )

        success = task_repo.create_task(task)

        tasks_created.append({
            "name": "Canvas Check",
            "id": task_id,
            "runs_at": next_run.strftime("%H:%M:%S")
        })

        print(f"[+] Task 1: Canvas Check")
        print(f"    ID: {task_id}")
        print(f"    Runs at: {next_run.strftime('%H:%M:%S')}")

    # Task 2: Web search (if configured)
    brave_key = config.get_secret("BRAVE_API_KEY")
    if brave_key:
        # Create task that runs 2 minutes from now
        next_run = datetime.now() + timedelta(minutes=2)
        task_id = str(uuid.uuid4())

        task = AgentTask(
            task_id=task_id,
            name="e2e_web_search",
            description="Search for 'Python asyncio' using search_web_brave with count=3. Report result count.",
            trigger=TaskTrigger.AT,
            schedule=next_run.isoformat(),
            status=TaskStatus.SCHEDULED,
            next_run=next_run,
            metadata={"test": True, "e2e": True}
        )

        success = task_repo.create_task(task)

        tasks_created.append({
            "name": "Web Search",
            "id": task_id,
            "runs_at": next_run.strftime("%H:%M:%S")
        })

        print(f"[+] Task 2: Web Search")
        print(f"    ID: {task_id}")
        print(f"    Runs at: {next_run.strftime('%H:%M:%S')}")

    # Task 3: Simple recurring task (always works)
    next_run = datetime.now() + timedelta(minutes=3)
    task_id = str(uuid.uuid4())

    task = AgentTask(
        task_id=task_id,
        name="e2e_recurring_test",
        description="Respond with 'Test task executed at [timestamp]'",
        trigger=TaskTrigger.EVERY,
        schedule="5m",  # Every 5 minutes
        status=TaskStatus.SCHEDULED,
        next_run=next_run,
        metadata={"test": True, "e2e": True, "executions": 0}
    )

    success = task_repo.create_task(task)

    tasks_created.append({
        "name": "Recurring Test",
        "id": task_id,
        "runs_at": next_run.strftime("%H:%M:%S"),
        "repeat": "every 5 minutes"
    })

    print(f"[+] Task 3: Recurring Test")
    print(f"    ID: {task_id}")
    print(f"    First run: {next_run.strftime('%H:%M:%S')}")
    print(f"    Repeats: every 5 minutes")

    # Summary
    print("\n" + "="*70)
    print("TASKS CREATED")
    print("="*70)

    if not tasks_created:
        print("\n[!] No tasks created - no API credentials configured")
        print("    Configure Canvas or Brave Search to enable tasks")
        return False

    print(f"\nCreated {len(tasks_created)} test tasks:\n")

    for i, task in enumerate(tasks_created, 1):
        print(f"{i}. {task['name']}")
        print(f"   ID: {task['id']}")
        print(f"   Runs at: {task['runs_at']}")
        if 'repeat' in task:
            print(f"   Repeats: {task['repeat']}")
        print()

    # Instructions
    print("="*70)
    print("NEXT STEPS")
    print("="*70)

    print("\n1. START CHAD:")
    print("   python main.py")
    print("   (or use the GUI)")

    print("\n2. WAIT FOR EXECUTION:")
    print("   The cron scheduler polls every 60 seconds")
    print("   Tasks will execute at their scheduled times")

    print("\n3. VERIFY EXECUTION:")
    print("   Check logs for task execution messages")
    print("   Or query task status:")
    print(f"   >>> from src.infrastructure.repositories.task_repository import TaskRepository")
    print(f"   >>> repo = TaskRepository('{db_path}')")
    print(f"   >>> task = repo.get_task('{tasks_created[0]['id']}')")
    print(f"   >>> print(task.status, task.last_run)")

    print("\n4. CLEANUP:")
    print("   Run: python tests/cleanup_test_tasks.py")

    print("\n" + "="*70)

    return True


def list_existing_tasks():
    """List all existing tasks in the database."""
    print("\n" + "="*70)
    print("EXISTING TASKS IN DATABASE")
    print("="*70)

    config = ConfigManager()
    db_path = config.get("task_db_path", "data/tasks.db")

    task_repo = TaskRepository(db_path)
    tasks = task_repo.get_scheduled_tasks()

    if not tasks:
        print("\nNo tasks found in database.")
        return

    print(f"\nFound {len(tasks)} tasks:\n")

    for i, task in enumerate(tasks, 1):
        print(f"{i}. {task.name}")
        print(f"   ID: {task.task_id}")
        print(f"   Status: {task.status.value}")
        print(f"   Trigger: {task.trigger.value}")
        print(f"   Schedule: {task.schedule}")
        print(f"   Next run: {task.next_run}")
        print(f"   Last run: {task.last_run}")
        if task.metadata.get("test"):
            print(f"   [TEST TASK]")
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="E2E Test - Create and manage test tasks")
    parser.add_argument("--list", action="store_true", help="List existing tasks")
    parser.add_argument("--create", action="store_true", help="Create new test tasks")

    args = parser.parse_args()

    if args.list:
        list_existing_tasks()
    elif args.create or not any([args.list, args.create]):
        # Default action: create tasks
        success = create_test_tasks()
        sys.exit(0 if success else 1)
