"""
benchmark_tool_selectors.py — Multi-Model Tool Selector Benchmark

Compares tool-selection accuracy, bloat, and latency across local models
(e.g., Qwen 3.5 9B vs. Salesforce xLAM-1b vs. Qwen 2.5 Coder).

Usage:
    # Benchmark currently loaded model:
    python evals/tools/benchmark_tool_selectors.py

    # Benchmark a specific model in LM Studio:
    python evals/tools/benchmark_tool_selectors.py --model "qwen/qwen3.5-9b"
    python evals/tools/benchmark_tool_selectors.py --model "QuantFactory/xLAM-1b-fc-r-GGUF"

    # Test with simulated lean RAG candidate shortlist (~10-12 tools):
    python evals/tools/benchmark_tool_selectors.py --mode rag

    # Test with full catalog (76 tools):
    python evals/tools/benchmark_tool_selectors.py --mode full
"""

import argparse
import ast
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

# ---------------------------------------------------------------------------
# 10 Representative Test Cases
# ---------------------------------------------------------------------------
BENCHMARK_PROMPTS = [
    {
        "id": 1,
        "category": "Canvas",
        "query": "What assignments do I have due this week in Canvas?",
        "expected": ["get_canvas_dashboard_todos"],
        "rag_candidates": [
            "get_canvas_dashboard_todos", "get_upcoming_assignments",
            "get_canvas_courses", "get_course_modules", "get_recent_grades",
            "read_course_file", "search_browser_history", "google_calendar_check",
        ],
    },
    {
        "id": 2,
        "category": "Canvas",
        "query": "Check my recent grades in Canvas and download the syllabus for CS 101",
        "expected": ["get_recent_grades", "download_course_file"],
        "rag_candidates": [
            "get_recent_grades", "get_course_grades", "download_course_file",
            "read_course_file", "get_course_syllabus", "get_course_files",
            "download_file", "list_directory",
        ],
    },
    {
        "id": 3,
        "category": "Gmail",
        "query": "Check my Gmail inbox for any new emails from John",
        "expected": ["search_gmail"],
        "rag_candidates": [
            "search_gmail", "gmail_check_inbox", "get_gmail_history",
            "draft_gmail_reply", "create_gmail_draft", "search_contacts",
            "send_email", "google_calendar_check",
        ],
    },
    {
        "id": 4,
        "category": "Compound Email",
        "query": "Draft a reply to Sarah's latest email saying I'll attend the meeting tomorrow",
        "expected": ["draft_gmail_reply"],
        "rag_candidates": [
            "draft_gmail_reply", "create_gmail_draft", "send_email",
            "search_gmail", "search_contacts", "google_calendar_check",
        ],
    },
    {
        "id": 5,
        "category": "System / App",
        "query": "Open Spotify and turn down the system volume a bit",
        "expected": ["open_app", "control_volume"],
        "rag_candidates": [
            "open_app", "control_volume", "system_settings", "open_browser",
            "run_routine", "youtube_desktop_control",
        ],
    },
    {
        "id": 6,
        "category": "Media / Smart Home",
        "query": "Mute YouTube playback and set the bedroom lights to 40%",
        "expected": ["youtube_desktop_control", "hubspace_control"],
        "rag_candidates": [
            "youtube_desktop_control", "hubspace_control", "control_volume",
            "system_settings", "capture_screen", "youtube_toolkit",
        ],
    },
    {
        "id": 7,
        "category": "File Ops",
        "query": "Find my resume in Documents, zip it, and email it to recruiter@tech.com",
        "expected": ["fuzzy_find_local", "archive_manager", "send_email"],
        "rag_candidates": [
            "fuzzy_find_local", "archive_manager", "send_email", "file_manager",
            "read_file", "list_directory", "create_gmail_draft", "search_contacts",
        ],
    },
    {
        "id": 8,
        "category": "Execution / Code",
        "query": "Run a Python script to calculate the first 20 fibonacci numbers",
        "expected": ["run_sandboxed_python"],
        "rag_candidates": [
            "run_sandboxed_python", "run_host_command", "run_sandboxed_bash",
            "run_script", "write_file", "install_package",
        ],
    },
    {
        "id": 9,
        "category": "Timed Alert",
        "query": "Remind me in 45 minutes to take a break from the screen",
        "expected": ["set_reminder"],
        "rag_candidates": [
            "set_reminder", "execute_future_task", "manage_cron",
            "manage_heartbeat", "send_telegram_message",
        ],
    },
    {
        "id": 10,
        "category": "No-Tool / Conversational",
        "query": "Can you explain how backpropagation works in simple terms?",
        "expected": [],
        "rag_candidates": [
            "open_browser", "web_search", "run_sandboxed_python", "get_datetime",
            "search_browser_history", "manage_user_memory",
        ],
    },
]

# ---------------------------------------------------------------------------
# Tool Catalog (Core + Common Tools)
# ---------------------------------------------------------------------------
TOOL_CATALOG: Dict[str, str] = {
    "archive_manager": "Compress folders into ZIP files or extract archives (.zip, .tar, .tar.gz).",
    "ask_user_question": "Call when you need to ask the user clarifying questions before proceeding.",
    "call_mcp_tool": "Execute a tool on an MCP server. Discover available servers and schemas first.",
    "capture_screen": "Capture a screenshot of the user's desktop screen.",
    "control_volume": "Control SYSTEM master volume (set, increase, decrease, mute, unmute).",
    "create_gmail_draft": "Create a new draft email when starting a new conversation.",
    "download_course_file": "Download a Canvas course file directly to local computer.",
    "download_file": "Download a file from a URL to a local path.",
    "draft_gmail_reply": "Create a draft reply to an email thread without sending immediately.",
    "execute_future_task": "Schedule an action for autonomous execution in N minutes.",
    "file_manager": "Perform filesystem operations: move, rename, delete files, create folders.",
    "fuzzy_find_local": "Search for a file inside a specific folder tree using fuzzy matching.",
    "get_assignment_details": "Fetch full instructions and content for a specific Canvas assignment.",
    "get_canvas_courses": "List all current active Canvas courses.",
    "get_canvas_dashboard_todos": "Get global To-Do list (all upcoming assignments, quizzes) from Canvas dashboard.",
    "get_course_files": "List files in a Canvas course (lecture notes, syllabus, attachments).",
    "get_course_grades": "Retrieve full gradebook breakdown for a Canvas course.",
    "get_course_modules": "List all modules in a Canvas course.",
    "get_course_syllabus": "Read the syllabus body directly from Canvas course settings.",
    "get_datetime": "Get the current date and/or time.",
    "get_gmail_history": "Get email changes starting from a specific historyId.",
    "get_recent_grades": "Retrieve the most recent graded submissions for a Canvas course.",
    "get_upcoming_assignments": "Get upcoming assignments for a specific Canvas course ID.",
    "gmail_check_inbox": "Check Gmail inbox for unread emails with specific keywords.",
    "google_calendar_check": "Check for Google Calendar events in next N hours.",
    "hubspace_control": "Control Hubspace smart lights, lamps, and devices (turn on/off, set brightness).",
    "install_package": "Install a Python package inside the sandbox.",
    "list_directory": "List directory contents in a tree-like visual format.",
    "manage_cron": "Manage autonomous scheduled background tasks.",
    "manage_heartbeat": "Manage periodic monitoring checks performed every 30 mins.",
    "manage_user_memory": "Store or retrieve facts in the user's personal knowledge graph.",
    "open_app": "Launch desktop applications (Spotify, Discord, Chrome, Slack, Notepad) by name.",
    "open_browser": "Launch default browser to open a website or web search.",
    "read_course_file": "Read content of a Canvas file (PDF, Word, text).",
    "read_file": "Read text contents of a local file (.txt, .md, .py, .json).",
    "run_host_command": "Execute shell commands or scripts on the host system.",
    "run_routine": "Execute a predefined multi-step routine or macro.",
    "run_sandboxed_bash": "Execute a bash command in the secure sandbox.",
    "run_sandboxed_python": "Execute Python code in a secure, isolated sandbox.",
    "run_script": "Run a local script file (.bat, .ps1, .vbs).",
    "search_browser_history": "Search Chrome browser history for pages or videos.",
    "search_contacts": "Search Google contacts by name to find email addresses.",
    "search_gmail": "READ: Fetch and search recent emails from Gmail.",
    "send_email": "WRITE: Send an email via Gmail directly.",
    "send_telegram_message": "Send a notification or alert via Telegram.",
    "set_reminder": "Schedule a timed notification or alert for the user.",
    "system_settings": "Control local PC hardware settings like brightness and volume.",
    "web_search": "Search the live web for answers and current information.",
    "write_file": "Create a new text file with specified content.",
    "youtube_desktop_control": "Control YouTube playback (play/pause, mute, skip) in browser.",
}

SYSTEM_PROMPT = """\
You are an ultra-precise tool selector for an AI assistant.
Given a user query and a catalog of available tools, return ONLY a raw JSON array \
containing the names of the tools directly needed to fulfill the request.

Rules:
- Output MUST be a valid JSON array of strings, e.g. ["tool_a", "tool_b"].
- If no tools are required (e.g. conversational question, general explanation), output [].
- Only include tools directly required to fulfill the user's intent. Do NOT include tangentially related tools.
- Never explain your choices. Return ONLY the JSON array.
"""


def call_selector(
    model_id: str,
    query: str,
    catalog: Dict[str, str],
    timeout: float = 60.0,
) -> Tuple[List[str], float, Optional[str]]:
    """Calls LM Studio and measures exact inference latency."""
    if "xlam" in model_id.lower():
        xlam_tools = [
            {"name": k, "description": v, "parameters": {"query": {"type": "string", "description": "target or query"}}}
            for k, v in catalog.items()
        ]
        task_instruction = (
            "You are an expert in composing functions. You are given a question and a set of possible functions. "
            "Based on the question, you will need to make one or more function/tool calls to achieve the purpose. "
            "If none of the functions can be used, return an empty list for tool_calls."
        )
        format_instruction = (
            'The output MUST strictly adhere to the following JSON format, and NO other text MUST be included. '
            'If no function call is needed, return empty list [] for tool_calls:\n'
            '{\n'
            '    "tool_calls": [\n'
            '        {"name": "func_name1", "arguments": {"query": "value1"}}\n'
            '    ]\n'
            '}'
        )
        xlam_prompt = (
            f"[BEGIN OF TASK INSTRUCTION]\n{task_instruction}\n[END OF TASK INSTRUCTION]\n\n"
            f"[BEGIN OF AVAILABLE TOOLS]\n{json.dumps(xlam_tools)}\n[END OF AVAILABLE TOOLS]\n\n"
            f"[BEGIN OF FORMAT INSTRUCTION]\n{format_instruction}\n[END OF FORMAT INSTRUCTION]\n\n"
            f"[BEGIN OF QUERY]\n{query}\n[END OF QUERY]\n\n"
        )
        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": xlam_prompt}],
            "temperature": 0.0,
            "max_tokens": 512,
        }
    else:
        catalog_json = "\n".join(f'  "{k}": "{v}"' for k, v in catalog.items())
        user_msg = f'User query: "{query}"\n\nAvailable tools:\n{{\n{catalog_json}\n}}'
        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
        }
        if "qwen" in model_id.lower():
            payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    req = urllib.request.Request(
        LM_STUDIO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = time.perf_counter() - t0
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            # Clean markdown code blocks or think tags
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```(?:json)?\s*", "", content).strip()
            content = re.sub(r"\s*```$", "", content).strip()
            if not content:
                return [], latency, None
            parsed = None
            try:
                parsed = json.loads(content)
            except Exception:
                try:
                    parsed = ast.literal_eval(content)
                except Exception:
                    # Regex match tool names inside quotes
                    matches = re.findall(r'["\']([a-zA-Z0-9_-]+)["\']', content)
                    if matches:
                        parsed = matches

            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except Exception:
                    try:
                        parsed = ast.literal_eval(parsed)
                    except Exception:
                        pass

            def _extract_names(obj) -> List[str]:
                found = []
                if isinstance(obj, list):
                    for item in obj:
                        found.extend(_extract_names(item))
                elif isinstance(obj, dict):
                    if "tool" in obj and isinstance(obj["tool"], str):
                        found.append(obj["tool"])
                    elif "name" in obj and isinstance(obj["name"], str):
                        found.append(obj["name"])
                    elif "tools" in obj and isinstance(obj["tools"], list):
                        found.extend(_extract_names(obj["tools"]))
                    else:
                        for k, v in obj.items():
                            if k in catalog or k in TOOL_CATALOG:
                                found.append(k)
                            elif isinstance(v, (dict, list)):
                                found.extend(_extract_names(v))
                elif isinstance(obj, str):
                    if obj in catalog or obj in TOOL_CATALOG:
                        found.append(obj)
                return found

            extracted = _extract_names(parsed)
            # Remove duplicates preserving order
            seen = set()
            clean_list = [x for x in extracted if not (x in seen or seen.add(x))]
            return clean_list, latency, None
    except Exception as e:
        latency = time.perf_counter() - t0
        return [], latency, str(e)


def evaluate_case(predicted: List[str], expected: List[str]) -> Dict[str, Any]:
    pred_set = set(predicted)
    exp_set = set(expected)
    tp = len(pred_set & exp_set)
    fp = len(pred_set - exp_set)
    fn = len(exp_set - pred_set)

    precision = tp / len(pred_set) if pred_set else (1.0 if not exp_set else 0.0)
    recall = tp / len(exp_set) if exp_set else (1.0 if not pred_set else 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "bloat": list(pred_set - exp_set),
        "missed": list(exp_set - pred_set),
        "exact_match": (pred_set == exp_set),
    }


def run_benchmark(model_id: str, mode: str = "rag") -> Dict[str, Any]:
    print("\n" + "=" * 78, flush=True)
    print(f" TOOL SELECTOR BENCHMARK: {model_id}", flush=True)
    print(f" Mode: {mode.upper()} ({'10-12 lean candidates' if mode == 'rag' else 'Full 50+ tool catalog'})", flush=True)
    print("=" * 78, flush=True)

    results = []
    total_lat = 0.0
    total_f1 = 0.0
    exact_matches = 0

    for tc in BENCHMARK_PROMPTS:
        query = tc["query"]
        expected = tc["expected"]
        if mode == "rag":
            catalog = {k: TOOL_CATALOG.get(k, "Tool") for k in tc["rag_candidates"] if k in TOOL_CATALOG}
        else:
            catalog = TOOL_CATALOG

        print(f"\n[#{tc['id']} - {tc['category']}] {query}")
        selected, latency, err = call_selector(model_id, query, catalog)
        eval_res = evaluate_case(selected, expected)

        total_lat += latency
        total_f1 += eval_res["f1"]
        if eval_res["exact_match"]:
            exact_matches += 1

        status = "✓ EXACT" if eval_res["exact_match"] else ("~ PARTIAL" if eval_res["f1"] > 0 else "✗ MISS")
        print(f"    Expected : {expected}")
        print(f"    Selected : {selected}")
        if eval_res["bloat"]:
            print(f"    Bloat    : +{eval_res['bloat']}")
        if eval_res["missed"]:
            print(f"    Missed   : -{eval_res['missed']}")
        if err:
            print(f"    Error    : {err}")
        print(f"    Result   : {status} | Latency: {latency:.2f}s | F1: {eval_res['f1']:.2f}")

        results.append({
            "id": tc["id"],
            "category": tc["category"],
            "query": query,
            "expected": expected,
            "selected": selected,
            "latency": latency,
            "eval": eval_res,
            "error": err,
        })

    n = len(BENCHMARK_PROMPTS)
    avg_lat = total_lat / n
    avg_f1 = total_f1 / n
    accuracy = (exact_matches / n) * 100.0

    print("\n" + "-" * 78)
    print(f" SUMMARY FOR {model_id} ({mode.upper()} mode)")
    print(f"   Exact Match Accuracy : {exact_matches}/{n} ({accuracy:.1f}%)")
    print(f"   Average F1 Score     : {avg_f1:.2f}")
    print(f"   Average Latency      : {avg_lat:.2f}s per query")
    print(f"   Total Wall-Clock Time: {total_lat:.1f}s")
    print("-" * 78)

    return {
        "model": model_id,
        "mode": mode,
        "accuracy": accuracy,
        "exact_matches": exact_matches,
        "total": n,
        "avg_f1": avg_f1,
        "avg_latency": avg_lat,
        "total_time": total_lat,
        "details": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Multi-Model Tool Selector Benchmark")
    parser.add_argument("--model", type=str, default="qwen/qwen3.5-9b", help="Model ID in LM Studio")
    parser.add_argument("--mode", type=str, choices=["rag", "full"], default="rag", help="Catalog size mode")
    parser.add_argument("--output", type=str, default="", help="Optional JSON output path")
    args = parser.parse_args()

    res = run_benchmark(args.model, args.mode)
    if args.output:
        Path(args.output).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"Saved results to {args.output}")


if __name__ == "__main__":
    main()
