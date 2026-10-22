"""
Master E2E Test Runner - Optimized for Essential Tests
Runs the core scenarios to verify system health.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager

async def main():
    """Run the core E2E scenarios."""
    print("\n" + "="*70)
    print("CHAD MASTER TEST RUNNER")
    print("="*70)

    config = ConfigManager()
    
    # Import essential test flows
    try:
        from tests.scenarios.test_heartbeat_cron_flow import (
            test_canvas_tool_integration,
            test_gmail_tool_integration,
            test_brave_search_integration,
            test_heartbeat_with_real_orchestrator,
            test_recurring_cron_task
        )
    except ImportError as e:
        print(f"\n[!] ERROR: Could not import scenarios: {e}")
        return False

    # Define the core suite
    suite = [
        ("Heartbeat Integration", test_heartbeat_with_real_orchestrator),
        ("Recurring Cron Task", test_recurring_cron_task)
    ]

    # Add API-dependent tests if configured
    if config.get_secret("CANVAS_TOKEN"):
        suite.append(("Canvas API Integration", test_canvas_tool_integration))
    
    if os.path.exists("config/credentials.json"):
        suite.append(("Gmail API Integration", test_gmail_tool_integration))
    
    if config.get_secret("BRAVE_API_KEY"):
        suite.append(("Brave Search Integration", test_brave_search_integration))

    print(f"\n[+] Detected {len(suite)} runnable tests based on environment.")
    print("\nPress Ctrl+C within 3 seconds to cancel...")
    try:
        await asyncio.sleep(3)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return False

    print("\n" + "="*70)
    print("RUNNING MASTER SUITE")
    print("="*70)

    results = []
    for name, func in suite:
        try:
            print(f"\n[RUN] Running: {name}...")
            passed = await func()
            results.append((name, passed))
        except Exception as e:
            print(f"[-] {name} CRASHED: {e}")
            results.append((name, False))

    # Final Summary
    print("\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)
    
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        if not passed: all_passed = False
        print(f"{status}: {name}")

    print("\n" + "="*70)
    return all_passed

if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
        sys.exit(0)
