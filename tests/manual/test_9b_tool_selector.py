"""
v2: Three-mode comparison for Qwen 3.5 9B tool selection.

MODE A — Full registry (76 tools) sent directly to 9B.
MODE B — Simulated RAG candidates (~10-12 per query) filtered by 9B.
MODE C — Full registry with a stricter "no bloat" prompt variant.

Goal: find which mode hits 100% accuracy with zero bloat.

Usage:
    python tests/manual/test_9b_tool_selector_v2.py

Requires: LM Studio running at localhost:1234 with qwen/qwen3.5-9b loaded.
"""

import json, re, time, urllib.request, urllib.error
from typing import List, Dict, Tuple, Optional

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL_ID = "qwen/qwen3.5-9b"

# ---------------------------------------------------------------------------
# Full real tool catalog (76 tools extracted from src/tools)
# ---------------------------------------------------------------------------
FULL_CATALOG: Dict[str, str] = {
    "archive_manager": "Compress folders into ZIP files or extract archives (.zip, .tar, .tar.gz). Use this for zipping project folders for submission or extracting downloaded assignments.",
    "ask_user_question": "MANDATORY: Call when you need to ask the user clarifying questions before proceeding.",
    "call_mcp_tool": "Execute a tool on an MCP server. Discover available servers and schemas first.",
    "capture_screen": "Capture a screenshot. Use for 'look at this', 'what's on my screen', 'check my YouTube window', or any visual context.",
    "complete_step": "Marks the currently active plan step as successfully completed.",
    "control_volume": "Control SYSTEM master volume (set, increase, decrease, mute, unmute). Affects entire computer audio.",
    "create_gmail_draft": "WRITE: Create a new draft email. Use when starting a NEW conversation (not replying).",
    "switch_to_plan_mode": "Switch into plan mode for complex, multi-step tasks that need a structured plan before execution.",
    "download_course_file": "Download a Canvas file directly to your local computer.",
    "download_file": "Download a file from a URL to a local path.",
    "download_sandbox_file": "Download a file generated inside the sandbox to the local host.",
    "draft_gmail_reply": "WRITE: Create a draft reply to an email thread. The draft can be reviewed before sending.",
    "execute_approved_plan": "Execute a file plan after user approval. Pass the approval_token the user provided.",
    "execute_future_task": "Delegate an action to your future self. Use when user says 'In 10 minutes, do X'. Schedules a command for autonomous execution.",
    "fail_step": "Marks the currently active plan step as failed.",
    "file_manager": "Perform filesystem operations: move, rename, delete files, or create folders. Requires approval_token for destructive actions.",
    "find_project": "Quickly search for a coding project by name in standard locations. Much faster than a full disk search.",
    "fuzzy_find_local": "Search for a file inside a specific folder tree using fuzzy matching. Avoids system folders and binary files.",
    "get_assignment_details": "Fetch the full instructions and content for a specific Canvas assignment.",
    "get_canvas_courses": "List all current active Canvas courses. Useful for finding course IDs.",
    "get_canvas_dashboard_todos": "Get global To-Do list (all upcoming assignments, quizzes, etc.) from Canvas dashboard. Use for 'what is due' queries.",
    "get_course_announcements": "Fetch the last 3 announcements for a Canvas course.",
    "get_course_files": "List files in a Canvas course (lecture notes, syllabus, assignment attachments).",
    "get_course_modules": "List all modules in a Canvas course. Modules organize content into weeks, units, or topics.",
    "get_course_pages": "List all wiki pages in a Canvas course (course policies, resources, study guides).",
    "get_course_people": "List all people enrolled in a Canvas course — students, instructors, TAs. Can search by name.",
    "get_course_syllabus": "Read the syllabus body directly from the course settings.",
    "get_datetime": "Get the current date and/or time.",
    "get_gmail_history": "Get specific email changes starting from a historyId. Use when a Pub/Sub notification provides a history_id.",
    "get_module_items": "List items within a specific Canvas module (assignments, files, pages, sub-headers).",
    "search_gmail": "READ: Fetch recent emails from Gmail. Use for 'check gmail', 'read emails', or searching the inbox.",
    "get_recent_grades": "Retrieve the most recent graded submissions for a Canvas course.",
    "get_upcoming_assignments": "Get upcoming assignments for a SPECIFIC Canvas course ID. For general 'what is due' use get_canvas_dashboard_todos.",
    "gmail_check_inbox": "Check Gmail inbox for unread emails with specific keywords.",
    "google_calendar_check": "Check for calendar events in next N hours.",
    "hubspace_control": "Control Hubspace lights, lamps, and devices (turn on/off, set brightness).",
    "install_package": "Install a Python package (pip) inside the sandbox. Use before running code that requires external libraries.",
    "list_directory": "List directory contents in a tree-like visual format with file sizes and folder counts.",
    "list_skills": "Lists all available skills with their descriptions. Call BEFORE calling load_skill.",
    "load_skill": "Loads the full instruction set (SKILL.md) for a given skill into context for domain-specific guidance.",
    "manage_cron": "Manage autonomous background tasks. Use for periodic checks, scheduled actions.",
    "manage_heartbeat": "Manage periodic monitoring checks (emails, social, etc.) performed every 30 mins.",
    "manage_persona_rules": "Evolve persona or remember durable meta-rules by editing cloud records.",
    "manage_tool_configs": "Save or retrieve preferred settings or notes for specific tools.",
    "manage_user_memory": "Manage knowledge about the user. Store or retrieve facts in the personal knowledge graph.",
    "open_app": "PRIMARY tool for finding and launching desktop applications (Discord, Chrome, Spotify, Slack, Notepad, etc.) by name.",
    "open_browser": "Physically launch the default browser to open a website or perform a web search.",
    "open_file": "Open a local file (document, image, video, etc.) using fuzzy matching.",
    "pause_for_input": "Pauses plan execution and surfaces a question or decision to the user. Execution resumes on user's next message.",
    "propose_file_plan": "REQUIRED before batch file operations. Returns a markdown plan and approval token.",
    "read_course_file": "Read the content of a Canvas file. Supports PDF, Word (.docx), and plain text.",
    "read_course_page": "Read the full content of a Canvas wiki page.",
    "read_file": "Read the text contents of a local file. Supports .txt, .md, .py, .json, .yaml, .csv.",
    "refresh_browser_history": "Manually refresh and sync the user's latest browser/Chrome history into memory.",
    "refresh_google_auth": "Refresh Google Workspace/Gmail credentials when other workspace tools fail with auth errors.",
    "resolve_path": "Resolve a user-friendly path alias (e.g., 'Downloads', 'Desktop', '~') to an absolute system path.",
    "run_host_command": "Execute shell commands, run git operations, and terminal scripts on the host system.",
    "run_routine": "Execute a predefined multi-step routine (macro) from the configuration (e.g., 'start_work', 'shutdown').",
    "run_sandboxed_bash": "Execute a bash command in the secure E2B sandbox for shell scripts or advanced OS tasks.",
    "run_sandboxed_python": "Execute Python code in a secure, isolated sandbox. Use for data analysis, document generation, or complex logic.",
    "run_script": "Run a local script file (.vbs, .bat, .ps1) using fuzzy matching.",
    "scroll": "Scrolls the active window or a target window up or down.",
    "search_browser_history": "Search the user's browser/Chrome history (YouTube videos, GitHub repos, web pages, Google searches).",
    "search_contacts": "Search Google contacts by name to find email addresses.",
    "search_module_items": "Search for items (pages, files, assignments) across ALL modules in a Canvas course.",
    "self_control": "Manage the AI Assistant's process (shutdown, restart).",
    "send_email": "WRITE: Send an email via Gmail. Only use when explicitly asked to 'send' or 'email' someone. Requires confirmation.",
    "send_telegram_message": "Send a message to the user via Telegram for notifications, reminders, or urgent alerts.",
    "set_reminder": "Schedule a future notification or alert. CANNOT perform actions or call other tools. Use only for timed notifications.",
    "suggest_mcp_install": "Open the MCP server install dialog when you need a capability that isn't currently configured.",
    "system_monitor": "Monitor the status of background services like Cron and Heartbeat. Check scheduled tasks and system health.",
    "system_settings": "Control local PC hardware settings like monitor brightness and system audio volume. Does NOT control smart home devices.",
    "test_toolkit": "Diagnostic tools for system verification.",
    "twitter_check_user": "Check if a specific Twitter user posted in a given timeframe.",
    "wait": "Pauses execution for a specified number of seconds after launching an application or process.",
    "write_file": "Create a text file with specified content in a directory.",
    "youtube_check_channel": "Check if a YouTube channel uploaded new videos.",
    "youtube_desktop_control": "EXCLUSIVE tool for controlling YouTube playback (mute, volume, play/pause) in the user's browser.",
    "youtube_toolkit": "API: Fetch transcripts and metadata for YouTube videos. Use for summarizing or processing video content.",
}

# ---------------------------------------------------------------------------
# Test cases — 12 queries, mix of simple, compound, and tricky near-misses
# Ground truth is what a perfect agent would call.
# RAG candidates simulate what vector search would return as a wider candidate pool.
# ---------------------------------------------------------------------------
TEST_CASES = [
    {
        "query": "Check my Gmail inbox",
        "expected": ["search_gmail"],
        "rag_candidates": ["search_gmail", "gmail_check_inbox", "get_gmail_history",
                           "draft_gmail_reply", "create_gmail_draft", "manage_heartbeat"],
    },
    {
        "query": "Send an email to John about the meeting tomorrow",
        "expected": ["send_email", "search_contacts"],
        "rag_candidates": ["send_email", "create_gmail_draft", "draft_gmail_reply",
                           "search_contacts", "search_gmail", "google_calendar_check"],
    },
    {
        "query": "Open Spotify",
        "expected": ["open_app"],
        "rag_candidates": ["open_app", "open_browser", "run_routine", "open_file"],
    },
    {
        "query": "Mute YouTube and turn the bedroom lights to 30%",
        "expected": ["youtube_desktop_control", "hubspace_control"],
        "rag_candidates": ["youtube_desktop_control", "control_volume", "system_settings",
                           "hubspace_control", "capture_screen", "youtube_toolkit"],
    },
    {
        "query": "Find my resume in Documents, zip it, and email it to Sarah",
        "expected": ["fuzzy_find_local", "archive_manager", "send_email", "search_contacts"],
        "rag_candidates": ["fuzzy_find_local", "read_file", "list_directory", "archive_manager",
                           "file_manager", "propose_file_plan", "send_email", "create_gmail_draft",
                           "search_contacts", "draft_gmail_reply"],
    },
    {
        "query": "What assignments do I have due this week?",
        "expected": ["get_canvas_dashboard_todos"],
        "rag_candidates": ["get_canvas_dashboard_todos", "get_upcoming_assignments",
                           "get_canvas_courses", "get_course_modules", "get_recent_grades"],
    },
    {
        "query": "Remind me in 30 minutes to take my medication",
        "expected": ["set_reminder"],
        "rag_candidates": ["set_reminder", "execute_future_task", "manage_cron",
                           "send_telegram_message", "manage_heartbeat"],
    },
    {
        "query": "Run this Python script and show me the output",
        "expected": ["run_sandboxed_python"],
        "rag_candidates": ["run_sandboxed_python", "run_host_command", "run_sandboxed_bash",
                           "run_script", "install_package"],
    },
    {
        "query": "What's the weather like?",
        "expected": [],  # No tool covers this — should return empty
        "rag_candidates": ["open_browser", "search_browser_history", "capture_screen", "get_datetime"],
    },
    {
        "query": "Check my emails, draft a reply to the latest one from Sarah saying I'll be there at 3pm, and remind me to send it",
        "expected": ["search_gmail", "draft_gmail_reply", "set_reminder"],
        "rag_candidates": ["search_gmail", "gmail_check_inbox", "draft_gmail_reply",
                           "create_gmail_draft", "send_email", "search_contacts",
                           "set_reminder", "execute_future_task", "manage_cron"],
    },
    {
        "query": "Dim the lights, pause YouTube, and set a reminder to sleep in 2 hours",
        "expected": ["hubspace_control", "youtube_desktop_control", "set_reminder"],
        "rag_candidates": ["hubspace_control", "system_settings", "youtube_desktop_control",
                           "control_volume", "set_reminder", "execute_future_task", "manage_cron"],
    },
    {
        "query": "What's in my Downloads folder?",
        "expected": ["list_directory"],
        "rag_candidates": ["list_directory", "fuzzy_find_local", "resolve_path",
                           "read_file", "file_manager"],
    },
]

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_STANDARD = """\
You are a precise tool selector for an AI assistant.

Given a user query and a catalog of available tools, return ONLY a JSON array \
of tool names that are directly needed to fulfill the request.

Rules:
- Return ONLY a raw JSON array, e.g. ["tool_a", "tool_b"]
- No explanation, no markdown, no extra text
- Only include tools that would actually be called — not tangentially related ones
- If no tools are needed, return []
- Tool names must match exactly as listed"""

SYSTEM_PROMPT_STRICT = """\
You are an ultra-precise tool selector. Your job: find the MINIMUM set of tools \
needed to directly fulfill the user's request. Every extra tool you include is a mistake.

Rules:
- Return ONLY a raw JSON array of tool name strings, nothing else
- Include a tool ONLY if it would definitely be called in the execution flow
- Prefer the most specific tool over a generic one
- Do NOT include fallbacks, alternatives, or "might be useful" tools
- If the request needs no tools (e.g. pure conversation), return []
- Tool names must match exactly"""


def build_user_message(query: str, catalog: Dict[str, str]) -> str:
    lines = "\n".join(f'  "{n}": "{d}"' for n, d in catalog.items())
    return f'User query: "{query}"\n\nAvailable tools:\n{{\n{lines}\n}}'


def call_model(query: str, catalog: Dict[str, str], system_prompt: str) -> Tuple[List[str], float]:
    payload = json.dumps({
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_message(query, catalog)},
        ],
        "temperature": 0.0,
        "max_tokens": 1024,
    }).encode("utf-8")

    req = urllib.request.Request(
        LM_STUDIO_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            latency = time.perf_counter() - t0
            body = json.loads(resp.read())
            raw = body["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw).strip()
            raw = re.sub(r"\s*```$", "", raw).strip()
            if not raw:
                return [], latency
            selected = json.loads(raw)
            return [str(t) for t in selected] if isinstance(selected, list) else [], latency
    except urllib.error.URLError as e:
        return [], time.perf_counter() - t0
    except json.JSONDecodeError:
        return [], time.perf_counter() - t0


def score(predicted: List[str], expected: List[str], all_tools: Dict[str, str]) -> dict:
    pred_set = set(predicted)
    exp_set = set(expected)
    tp = len(pred_set & exp_set)
    fp = len(pred_set - exp_set)
    fn = len(exp_set - pred_set)
    precision = tp / len(pred_set) if pred_set else (1.0 if not exp_set else 0.0)
    recall    = tp / len(exp_set)  if exp_set  else (1.0 if not pred_set else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    hallucinations = [t for t in predicted if t not in all_tools]
    return {"precision": precision, "recall": recall, "f1": f1,
            "fp": fp, "fn": fn, "hallucinations": hallucinations}


def run_mode(label: str, system_prompt: str, use_rag_candidates: bool):
    print(f"\n{'=' * 70}")
    print(f"  MODE: {label}")
    print(f"  Catalog size: {'~10-12 RAG candidates' if use_rag_candidates else str(len(FULL_CATALOG)) + ' tools (full registry)'}")
    print("=" * 70)

    total_p = total_r = total_f1 = total_lat = 0.0
    perfect = 0

    for i, tc in enumerate(TEST_CASES, 1):
        query    = tc["query"]
        expected = tc["expected"]
        catalog  = {k: FULL_CATALOG[k] for k in tc["rag_candidates"]} if use_rag_candidates else FULL_CATALOG

        selected, latency = call_model(query, catalog, system_prompt)
        s = score(selected, expected, FULL_CATALOG)

        total_p   += s["precision"]
        total_r   += s["recall"]
        total_f1  += s["f1"]
        total_lat += latency
        if s["f1"] == 1.0:
            perfect += 1

        status = "PASS" if s["f1"] >= 0.8 else "FAIL"
        print(f"\n[{i:02d}] {query!r}")
        print(f"      Expected : {expected}")
        print(f"      Selected : {selected}")
        if s["hallucinations"]:
            print(f"      HALLUCIN : {s['hallucinations']}")
        if s["fp"]:
            bloat = [t for t in selected if t not in set(expected)]
            print(f"      BLOAT    : {bloat}")
        if s["fn"]:
            missed = [t for t in expected if t not in set(selected)]
            print(f"      MISSED   : {missed}")
        print(f"      P={s['precision']:.2f}  R={s['recall']:.2f}  F1={s['f1']:.2f}  {latency:.1f}s  {status}")

    n = len(TEST_CASES)
    print(f"\n  --- {label} Summary ---")
    print(f"  Perfect (F1=1.0) : {perfect}/{n}")
    print(f"  Avg Precision    : {total_p/n:.2f}")
    print(f"  Avg Recall       : {total_r/n:.2f}")
    print(f"  Avg F1           : {total_f1/n:.2f}")
    print(f"  Avg Latency      : {total_lat/n:.1f}s")
    return {"label": label, "perfect": perfect, "f1": total_f1/n, "latency": total_lat/n}


def main():
    print("=" * 70)
    print("  Qwen 3.5 9B Tool Selection v2 — Three-Mode Comparison")
    print(f"  Model: {MODEL_ID}   |   {len(FULL_CATALOG)} total tools   |   {len(TEST_CASES)} queries")
    print("=" * 70)

    results = []
    results.append(run_mode("A: Full registry + standard prompt", SYSTEM_PROMPT_STANDARD, use_rag_candidates=False))
    results.append(run_mode("B: RAG candidates + standard prompt", SYSTEM_PROMPT_STANDARD, use_rag_candidates=True))
    results.append(run_mode("C: RAG candidates + strict no-bloat prompt", SYSTEM_PROMPT_STRICT, use_rag_candidates=True))

    print(f"\n{'=' * 70}")
    print("  FINAL COMPARISON")
    print("=" * 70)
    print(f"  {'Mode':<42} {'Perfect':>8} {'Avg F1':>8} {'Avg Lat':>9}")
    print(f"  {'-'*42} {'-'*8} {'-'*8} {'-'*9}")
    for r in results:
        n = len(TEST_CASES)
        print(f"  {r['label']:<42} {r['perfect']:>5}/{n}  {r['f1']:>6.2f}  {r['latency']:>7.1f}s")
    print()

    best = max(results, key=lambda r: (r["perfect"], r["f1"]))
    print(f"  Best mode: {best['label']}")
    if best["perfect"] == len(TEST_CASES):
        print("  VERDICT: 100% perfect — ready to integrate.")
    elif best["f1"] >= 0.88:
        print("  VERDICT: Strong — minor prompt tuning could reach 100%.")
    else:
        print("  VERDICT: Needs more work before production use.")
    print()


if __name__ == "__main__":
    main()
