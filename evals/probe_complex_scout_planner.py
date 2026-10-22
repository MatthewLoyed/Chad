# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
"""
Re-test Bonsai 27B with the improved planner reconnaissance prompt on the complex task.
"""
import json
import urllib.request
from pathlib import Path

# Load updated planner persona prompt
persona_path = Path("prompts/persona/planner.md")
planner_prompt_template = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""
if planner_prompt_template.startswith("---"):
    parts = planner_prompt_template.split("---", 2)
    planner_prompt_template = parts[2] if len(parts) >= 3 else planner_prompt_template

planner_system_prompt = planner_prompt_template.strip()
planner_system_prompt += (
    "\n\n### RUNTIME\n"
    "- allow_ask=True\n"
    "- scout_dispatches_used=0/2\n"
    "- dispatch_scout_bound=True\n"
    "### SCOUT GATHER BRIEF\n"
    "(none yet)\n"
    "### CONFIRMED TOOL MENU\n"
    "- `manage_user_memory`: Query or recall personal knowledge graph, family members, relationships, and user profile.\n"
    "- `search_contacts`: Search contacts book for people, names, phone numbers, and email addresses.\n"
    "- `search_gmail`: Search email history for messages, threads, recipients, and attachments.\n"
    "- `get_financial_summary`: Retrieve bank accounts, net worth, cash flow, and asset breakdown.\n"
    "- `get_recent_transactions`: Fetch recent transactions and category spending.\n"
    "- `render_chart`: Generate and render visual data charts (bar, pie, line) from data.\n"
    "- `show_youtube_video`: Search and retrieve relevant video links or embed info.\n"
    "- `write_file`: Write or format deliverables and reports to markdown/html files.\n"
    "- `send_email`: Send an email with body and attachments to a recipient.\n"
    "- `delegate_to_research_agent`: Web research for external information.\n"
    "- `delegate_to_traversal_agent`: Search and read local documents and files.\n"
)

# Define tools for LLM
tools = [
    {
        "type": "function",
        "function": {
            "name": "dispatch_scout",
            "description": "Dispatch a lightweight read-only scout pass to gather facts, environment data, contacts, files, or information before planning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {
                        "type": "string",
                        "description": "Detailed gather brief specifying exactly what information, facts, contacts, numbers, or sources to find."
                    }
                },
                "required": ["brief"]
            }
        }
    },
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
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user_question",
            "description": "Ask the user for clarification on genuine subjective preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question to ask."}
                },
                "required": ["question"]
            }
        }
    }
]

user_prompt = (
    "Make a report about my finances and my family, include a chart and a relevant video, "
    "and email Jordan the results."
)

payload = {
    "model": "prism-ml/bonsai-27b",
    "messages": [
        {"role": "system", "content": planner_system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    "tools": tools,
    "tool_choice": "auto",
    "temperature": 0.2,
    "max_tokens": 1500
}

print("Invoking Bonsai 27B with updated Reconnaissance Doctrine...")
req = urllib.request.Request(
    "http://localhost:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
        choice = result["choices"][0]["message"]
        tool_calls = choice.get("tool_calls") or []
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
