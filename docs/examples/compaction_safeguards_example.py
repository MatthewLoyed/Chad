"""
Compaction Safeguards - Usage Examples

Demonstrates how to use enhanced memory compaction with safeguards to preserve
critical context during memory compression (Pi/OpenClaw pattern).

Features:
    - Tool failure preservation
    - File operation tracking
    - Incomplete task detection
    - Adaptive token budgeting
    - Critical turn identification

Problem Solved:
    During memory compaction, important context can be lost:
    - Tool errors that need debugging
    - Files that were modified
    - Tasks that are incomplete
    - Critical user questions

Solution:
    Safeguards extract and preserve this critical information
    before compressing the conversation history.
"""

import asyncio
from src.core.utils.consolidation_manager import ConsolidationManager, CompactionSafeguards
from src.core.intelligence.memory_engine import MemoryEngine


# ==============================================================================
# BASIC USAGE
# ==============================================================================

async def example_basic_compaction():
    """
    Basic compaction with safeguards enabled.
    """
    # Initialize managers
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory, profile='balanced')

    # Example conversation history
    history = [
        {'role': 'user', 'content': 'Create a new file called test.py'},
        {'role': 'model', 'content': 'Creating test.py...'},
        {'role': 'tool_result', 'tool_name': 'file_write', 'content': 'File created: test.py'},
        {'role': 'user', 'content': 'Now run the tests'},
        {'role': 'tool_result', 'tool_name': 'pytest', 'success': False, 'error': 'ImportError: module not found'},
        {'role': 'model', 'content': 'There was an error. Working on fixing the import...'},
    ]

    # Perform enhanced consolidation with safeguards
    async def mock_generate(prompt, history, json_mode=False):
        return """
        {
            "completed_tasks": ["Created test.py"],
            "pending_tasks": ["Fix import error in tests"],
            "project_state": "Debugging test failures",
            "user_data": {},
            "system_changes": ["created: test.py"],
            "tool_failures": ["pytest: ImportError - module not found"],
            "file_operations": ["created: test.py"]
        }
        """

    result = await consolidation.perform_consolidation_with_safeguards(
        history=history,
        model_generate_fn=mock_generate
    )

    print("=== Compaction Result ===")
    print(result['wake_message'])
    print(f"\nSafeguards preserved:")
    print(f"  - Tool failures: {len(result['safeguards'].tool_failures)}")
    print(f"  - File operations: {len(result['safeguards'].file_operations)}")
    print(f"  - Incomplete tasks: {len(result['safeguards'].incomplete_tasks)}")


# ==============================================================================
# SAFEGUARD EXTRACTION
# ==============================================================================

def example_extract_safeguards():
    """
    Extract safeguards from conversation history.
    """
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    # Example history with various scenarios
    history = [
        # Tool failure
        {
            'role': 'tool_result',
            'tool_name': 'gmail_send',
            'success': False,
            'error': '401 Unauthorized - Invalid credentials',
            'timestamp': '2026-03-16T10:00:00'
        },

        # File operation
        {
            'role': 'model',
            'content': 'I created a new file called config.yaml with your settings.'
        },

        # Incomplete task
        {
            'role': 'model',
            'content': 'Working on: implementing the API endpoint. Next step: add authentication.'
        },

        # Critical user question
        {
            'role': 'user',
            'content': 'How do I configure the database connection?'
        },

        # Long important response
        {
            'role': 'model',
            'content': 'Here is a detailed explanation... ' + ('x' * 600)  # Long response
        }
    ]

    # Extract safeguards
    safeguards = consolidation.extract_safeguards(history)

    print("\n=== Extracted Safeguards ===")
    print(f"Tool failures: {len(safeguards.tool_failures)}")
    for failure in safeguards.tool_failures:
        print(f"  - {failure['tool_name']}: {failure['error']}")

    print(f"\nFile operations: {len(safeguards.file_operations)}")
    for op in safeguards.file_operations:
        print(f"  - {op['operation']}: {op['files']}")

    print(f"\nIncomplete tasks: {len(safeguards.incomplete_tasks)}")
    for task in safeguards.incomplete_tasks:
        print(f"  - {task}")

    print(f"\nCritical turns: {len(safeguards.critical_turn_ids)}")
    print(f"  Turn indices: {sorted(safeguards.critical_turn_ids)}")


# ==============================================================================
# ADAPTIVE BUDGET CALCULATION
# ==============================================================================

def example_adaptive_budget():
    """
    Calculate adaptive token budget based on turn importance.
    """
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    # Create example history
    history = [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'model', 'content': 'Hi! How can I help?'},
        {'role': 'tool_result', 'tool_name': 'search', 'success': False, 'error': 'Timeout'},
        {'role': 'user', 'content': 'Create test.py'},
        {'role': 'model', 'content': 'Created test.py successfully'},
        {'role': 'user', 'content': 'What was the error earlier?'},  # Critical question
    ] + [
        {'role': 'model', 'content': f'Turn {i}'} for i in range(20)  # Filler turns
    ]

    # Extract safeguards first
    safeguards = consolidation.extract_safeguards(history)

    # Calculate adaptive budget
    budget = consolidation.calculate_adaptive_budget(
        history=history,
        safeguards=safeguards,
        target_tokens=10000
    )

    print("\n=== Adaptive Budget ===")
    print(f"Compression ratio: {budget['compression_ratio']:.2f}")
    print(f"Target tokens: {budget['target_tokens']}")
    print(f"\nBudget allocation:")
    print(f"  Critical turns: {len(budget['critical_turns'])} (100% preserved)")
    print(f"  Tool failures: {len(budget['tool_failures'])} (80% preserved)")
    print(f"  File operations: {len(budget['file_operations'])} (60% preserved)")
    print(f"  Recent turns: {len(budget['recent_turns'])} (50% preserved)")
    print(f"  Compressible: {len(budget['compressible_turns'])} (20% preserved)")


# ==============================================================================
# FILE OPERATION TRACKING
# ==============================================================================

def example_file_operations():
    """
    Track file operations across conversation history.
    """
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    # Various file operation patterns
    test_contents = [
        'I created a new file called "config.yaml"',
        'Modified the file: src/main.py',
        'Deleted old_file.txt',
        'Saved changes to C:\\Users\\test\\document.docx',
        'Updated /home/user/script.sh with new code',
        'Wrote data to output.json',
    ]

    for content in test_contents:
        history = [{'role': 'model', 'content': content}]
        safeguards = consolidation.extract_safeguards(history)

        if safeguards.file_operations:
            op = safeguards.file_operations[0]
            print(f"\nContent: {content}")
            print(f"  Detected operation: {op['operation']}")
            print(f"  Files: {op['files']}")


# ==============================================================================
# INCOMPLETE TASK DETECTION
# ==============================================================================

def example_incomplete_tasks():
    """
    Detect incomplete tasks that should be preserved.
    """
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    # Various task patterns
    test_contents = [
        'Working on: implementing the authentication system',
        'In progress: refactoring the database layer',
        'Need to: add error handling to the API',
        'TODO: write unit tests for the new feature',
        'Pending: review the pull request',
        'Next step: deploy to staging environment',
    ]

    print("\n=== Incomplete Task Detection ===")
    for content in test_contents:
        history = [{'role': 'model', 'content': content}]
        safeguards = consolidation.extract_safeguards(history)

        if safeguards.incomplete_tasks:
            print(f"\nContent: {content}")
            print(f"  Extracted task: {safeguards.incomplete_tasks[0]}")


# ==============================================================================
# TOOL FAILURE PRESERVATION
# ==============================================================================

def example_tool_failures():
    """
    Preserve tool failures for debugging.
    """
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    # Simulate multiple tool failures
    history = [
        {
            'role': 'tool_result',
            'tool_name': 'gmail_send',
            'success': False,
            'error': '401 Unauthorized',
            'timestamp': '2026-03-16T10:00:00'
        },
        {
            'role': 'tool_result',
            'tool_name': 'brave_search',
            'success': False,
            'error': '429 Rate limit exceeded',
            'timestamp': '2026-03-16T10:05:00'
        },
        {
            'role': 'tool_result',
            'tool_name': 'file_write',
            'success': False,
            'error': 'Permission denied: /etc/config.txt',
            'timestamp': '2026-03-16T10:10:00'
        },
    ]

    safeguards = consolidation.extract_safeguards(history)

    print("\n=== Tool Failure Preservation ===")
    print(f"Total failures: {len(safeguards.tool_failures)}")

    for failure in safeguards.tool_failures:
        print(f"\n{failure['tool_name']}:")
        print(f"  Error: {failure['error']}")
        print(f"  Time: {failure['timestamp']}")
        print(f"  Turn: {failure['turn_index']}")


# ==============================================================================
# COMPACTION PROFILES
# ==============================================================================

def example_compaction_profiles():
    """
    Demonstrate different compaction profiles.
    """
    memory = MemoryEngine()

    profiles = ['aggressive', 'balanced', 'permissive']

    print("\n=== Compaction Profiles ===")
    for profile in profiles:
        consolidation = ConsolidationManager(memory, profile=profile)
        status = consolidation.get_status()

        print(f"\n{profile.upper()} Profile:")
        print(f"  Soft limit: {status['limits']['soft']:,} tokens")
        print(f"  Hard limit: {status['limits']['hard']:,} tokens")
        print(f"  Max limit: {status['limits']['max']:,} tokens")


# ==============================================================================
# ENHANCED WAKE-UP MESSAGE
# ==============================================================================

def example_wake_up_message():
    """
    Generate enhanced wake-up message with safeguards.
    """
    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    # Create sample data and safeguards
    data = {
        'project_state': 'Building REST API with authentication',
        'pending_tasks': ['Add error handling', 'Write tests', 'Deploy']
    }

    safeguards = CompactionSafeguards(
        tool_failures=[
            {'tool_name': 'pytest', 'error': 'ImportError', 'turn_index': 5}
        ],
        file_operations=[
            {'operation': 'created', 'files': ['api.py', 'auth.py'], 'turn_index': 3}
        ],
        incomplete_tasks=['Fix import error', 'Add logging']
    )

    message = consolidation._generate_wake_up_message(data, safeguards)

    print("\n=== Enhanced Wake-Up Message ===")
    print(message)


# ==============================================================================
# INTEGRATION EXAMPLE
# ==============================================================================

async def example_full_integration():
    """
    Complete integration example showing compaction workflow.
    """
    print("\n=== Full Compaction Workflow ===")

    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory, profile='balanced')

    # Simulate long conversation reaching soft limit
    history = []

    # Add initial conversation
    history.extend([
        {'role': 'user', 'content': 'Help me build a web API'},
        {'role': 'model', 'content': 'I can help! Let\'s start with the structure.'},
    ])

    # Add file operations
    history.extend([
        {'role': 'model', 'content': 'Created api.py with Flask setup'},
        {'role': 'tool_result', 'tool_name': 'file_write', 'content': 'File created: api.py'},
    ])

    # Add tool failure
    history.extend([
        {'role': 'model', 'content': 'Running tests...'},
        {'role': 'tool_result', 'tool_name': 'pytest', 'success': False, 'error': 'ModuleNotFoundError: flask'},
    ])

    # Add incomplete task
    history.extend([
        {'role': 'model', 'content': 'Working on: installing Flask. Next step: run tests again.'},
    ])

    # Add many filler turns to simulate approaching limit
    for i in range(50):
        history.append({'role': 'model', 'content': f'Intermediate step {i}...'})

    print(f"History length: {len(history)} turns")

    # Check if compaction needed
    current_tokens = sum(len(turn.get('content', '')) // 4 for turn in history)
    trigger = consolidation.check_triggers(current_tokens, len(history))

    print(f"Current tokens: ~{current_tokens}")
    print(f"Trigger status: {trigger}")

    if trigger in ['soft', 'hard']:
        print("\nCompaction triggered!")

        # Perform enhanced compaction
        async def mock_generate(prompt, history, json_mode=False):
            return """
            {
                "completed_tasks": ["Created API structure", "Set up Flask"],
                "pending_tasks": ["Install Flask", "Run tests", "Add authentication"],
                "project_state": "Building REST API with Flask",
                "user_data": {},
                "system_changes": ["created: api.py"],
                "tool_failures": ["pytest: ModuleNotFoundError - flask not installed"],
                "file_operations": ["created: api.py"]
            }
            """

        result = await consolidation.perform_consolidation_with_safeguards(
            history=history,
            model_generate_fn=mock_generate
        )

        print("\n" + result['wake_message'])
        print(f"\nMemory preserved critical context:")
        print(f"  - {len(result['safeguards'].tool_failures)} errors for debugging")
        print(f"  - {len(result['safeguards'].file_operations)} file operations tracked")
        print(f"  - {len(result['safeguards'].incomplete_tasks)} tasks preserved")


# ==============================================================================
# COMPARISON: WITH vs WITHOUT SAFEGUARDS
# ==============================================================================

async def example_comparison():
    """
    Compare compaction with and without safeguards.
    """
    print("\n=== WITH vs WITHOUT Safeguards ===")

    memory = MemoryEngine()
    consolidation = ConsolidationManager(memory)

    history = [
        {'role': 'tool_result', 'tool_name': 'api_call', 'success': False, 'error': '500 Server Error'},
        {'role': 'model', 'content': 'Created config.yaml'},
        {'role': 'model', 'content': 'Working on: debugging the API connection'},
    ]

    async def mock_generate(prompt, history, json_mode=False):
        return '{"completed_tasks": [], "pending_tasks": [], "project_state": "Unknown", "user_data": {}, "system_changes": []}'

    # Without safeguards (standard)
    print("\nStandard Compaction:")
    result_standard = await consolidation.perform_consolidation(
        history=history,
        model_generate_fn=mock_generate
    )
    print("  - Tool failures preserved: NO")
    print("  - File operations tracked: NO")
    print("  - Incomplete tasks: NO")

    # With safeguards (enhanced)
    print("\nEnhanced Compaction:")
    result_enhanced = await consolidation.perform_consolidation_with_safeguards(
        history=history,
        model_generate_fn=mock_generate
    )
    print(f"  - Tool failures preserved: YES ({len(result_enhanced['safeguards'].tool_failures)})")
    print(f"  - File operations tracked: YES ({len(result_enhanced['safeguards'].file_operations)})")
    print(f"  - Incomplete tasks: YES ({len(result_enhanced['safeguards'].incomplete_tasks)})")


# ==============================================================================
# MAIN
# ==============================================================================

async def main():
    """Run all examples."""
    print("=== Compaction Safeguards Examples ===\n")

    # Basic examples
    await example_basic_compaction()
    example_extract_safeguards()
    example_adaptive_budget()

    # Feature-specific examples
    example_file_operations()
    example_incomplete_tasks()
    example_tool_failures()

    # Advanced examples
    example_compaction_profiles()
    example_wake_up_message()
    await example_full_integration()
    await example_comparison()

    print("\n✓ All examples completed")


if __name__ == "__main__":
    asyncio.run(main())


# ==============================================================================
# USAGE NOTES
# ==============================================================================
"""
Benefits of Compaction Safeguards:

1. Error Debugging
   - Tool failures preserved with full context
   - Error messages retained for troubleshooting
   - Timestamp and turn index tracked

2. File Operation Tracking
   - Know exactly what files were modified
   - Track create/modify/delete operations
   - Maintain file operation history

3. Task Continuity
   - Incomplete tasks preserved
   - Work-in-progress not lost
   - Next steps remembered

4. Adaptive Budgeting
   - Critical turns fully preserved
   - Less important turns compressed
   - Intelligent token allocation

5. Enhanced Wake-Up
   - Context-rich resume messages
   - Immediate access to critical info
   - No information loss

When to Use:
- Long conversations approaching token limits
- Complex multi-step tasks
- Debugging scenarios with errors
- File-heavy operations
- Research and analysis sessions

Configuration:
- aggressive: Compact more frequently (30k soft limit)
- balanced: Standard compaction (120k soft limit)
- permissive: Rare compaction (400k soft limit)

Best Practices:
1. Use enhanced compaction for important sessions
2. Standard compaction for casual conversations
3. Review safeguards after compaction
4. Store tool failures for debugging
5. Track file operations for auditing
"""
