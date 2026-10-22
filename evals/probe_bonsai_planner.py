"""
Probe Bonsai 27B as Chad Planner - Round 3 (Answering the user question to get propose_plan)
"""
import json
import urllib.request
from pathlib import Path

# Load real planner persona prompt
persona_path = Path("prompts/persona/planner.md")
planner_prompt_template = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""
if planner_prompt_template.startswith("---"):
    parts = planner_prompt_template.split("---", 2)
    planner_prompt_template = parts[2] if len(parts) >= 3 else planner_prompt_template

planner_system_prompt = planner_prompt_template.strip()
planner_system_prompt += (
    "\n\n### RUNTIME\n"
    "- allow_ask=False\n"
    "- scout_dispatches_used=1/2\n"
    "- dispatch_scout_bound=False\n"
    "### SCOUT GATHER BRIEF\n"
    "1. Project Logging Configuration: Found central setup in `src/shared/logger.py`.\n"
    "2. Logger initialization: Found in `src/shared/logger.py`, `src/remote/server.py`, and `src/core/orchestration/supervisor.py`.\n"
    "3. Unrotated file writing: `src/tools/diagnostics/debug_sink.py` directly opens `data/logs/debug.log` without rotation.\n"
    "4. Test suite: Existing tests in `tests/unit/test_logger.py` using pytest.\n"
    "5. Target report path: `artifacts/logging_audit.md` does not exist yet.\n"
    "\n### CONFIRMED TOOL MENU\n"
    "- `delegate_to_traversal_agent`: Search and read local files, code, and project directories.\n"
    "- `delegate_to_code_agent`: Write or modify code files and run code scripts.\n"
    "- `delegate_to_research_agent`: Deep web research across multiple external sources.\n"
    "- `write_file`: Write or overwrite text / markdown deliverable files.\n"
    "- `read_file`: Inspect contents of an existing file.\n"
    "- `run_command`: Execute shell commands, linters, or test suites.\n"
)

# Define tools for LLM - with allow_ask=False, only propose_plan is available
tools = [
    {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": "Submit the concrete multi-step execution plan for user approval. Call this when ready to plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Ordered execution steps for the supervisor after approval.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Step id e.g. step_1"},
                                "description": {"type": "string", "description": "One concrete, verifiable DO action"},
                                "tools": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Tool names from the confirmed menu for this step, or [] for present-only"
                                },
                                "status": {"type": "string", "description": "pending"}
                            },
                            "required": ["description", "tools"]
                        }
                    }
                },
                "required": ["steps"]
            }
        }
    }
]

messages = [
    {"role": "system", "content": planner_system_prompt},
    {
        "role": "user",
        "content": (
            "I need an audit of our project's logging setup. Find all files where logger is initialized, "
            "check if any loggers are writing to unrotated files on disk, create a summary report with "
            "recommended fixes in artifacts/logging_audit.md, run the test suite to make sure logging tests "
            "still pass, and report the final outcome."
        )
    },
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_ask_1",
                "type": "function",
                "function": {
                    "name": "ask_user_question",
                    "arguments": json.dumps({
                        "question": "Before proceeding, I want to confirm: should I also check for any other logging-related files (e.g., configuration files like .env, logrotate configs, or other diagnostic tools) that might be relevant to the audit? Or should I focus only on the Python source code files mentioned in the initial findings?"
                    })
                }
            }
        ]
    },
    {
        "role": "tool",
        "tool_call_id": "call_ask_1",
        "name": "ask_user_question",
        "content": "Focus only on the Python source code files mentioned in the scout findings. Propose the execution plan now."
    }
]

payload = {
    "model": "prism-ml/bonsai-27b",
    "messages": messages,
    "tools": tools,
    "tool_choice": "auto",
    "temperature": 0.2,
    "max_tokens": 1500
}

print("Invoking Bonsai 27B for final propose_plan...")
req = urllib.request.Request(
    "http://localhost:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
        choice = result["choices"][0]["message"]
        print("\n=== MODEL CONTENT RESPONSE ===")
        print(choice.get("content") or "(No direct content)")
        
        print("\n=== TOOL CALLS EMITTED ===")
        tool_calls = choice.get("tool_calls") or []
        if not tool_calls:
            print("No tool calls emitted!")
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            print(f"Tool Name: {name}")
            try:
                args = json.loads(fn.get("arguments", "{}"))
                print("Arguments:")
                print(json.dumps(args, indent=2))
            except Exception:
                print("Raw Arguments:", fn.get("arguments"))
except Exception as e:
    print("Error during execution:", e)
