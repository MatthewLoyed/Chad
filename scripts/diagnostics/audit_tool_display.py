"""One-off audit: which tools lack UI/backend display formatters.

Convention: every native tool must implement format_action() or @tool_display
and have a matching entry in src/clients/web/src/utils/tool-ui.tsx.
See src/infrastructure/tools/README.md and src/tools/example_tool.py.example.
"""
import json
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

tool_names: set[str] = set()
backend_by_tool: set[str] = set()

for p in (ROOT / "src/tools").rglob("*.py"):
    text = p.read_text(encoding="utf-8", errors="ignore")
    names = re.findall(r'^\s+name\s*=\s*["\']([^"\']+)["\']', text, re.M)
    has_fmt = "@tool_display" in text or "def format_action" in text
    for n in names:
        tool_names.add(n)
        if has_fmt:
            backend_by_tool.add(n)

ui_text = (ROOT / "src/clients/web/src/utils/tool-ui.tsx").read_text(encoding="utf-8")
meta_keys = set(re.findall(r"^\s+([a-z0-9_]+):\s*\{\s*title:", ui_text, re.M))

dynamic = {
    "open_browser", "open_url", "read_url_content", "browser_subagent",
    "google_search", "search_web_brave", "search_web", "web_search",
    "show_youtube_video", "write_file", "read_file", "create_doc", "view_file",
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "list_dir", "list_directory", "hubspace_control", "load_skill", "run_command",
    "kanban_board", "call_mcp_tool",
}

covered = meta_keys | dynamic

freq: dict[str, int] = {}
for p in (ROOT / "evals/datasets").glob("*.jsonl"):
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        exp = obj.get("expect") or {}
        for key in ("tools_any", "tools_all", "tools"):
            for t in exp.get(key, []) or []:
                freq[t] = freq.get(t, 0) + 1

print(f"Total local tools: {len(tool_names)}")
print(f"UI covered (static+dynamic): {len(covered & tool_names)}")
print(f"Backend @tool_display/format_action: {len(backend_by_tool & tool_names)}")
print()

missing_ui = sorted(t for t in tool_names - covered if t not in backend_by_tool)
print("=== Tools missing BOTH custom backend formatter AND UI coverage ===")
for t in missing_ui:
    print(f"  {t}")
print()

print("=== Eval-frequent tools (3+ refs) — coverage status ===")
for t, n in sorted(freq.items(), key=lambda x: -x[1]):
    if n < 3 or t.startswith("delegate_to_"):
        continue
    ui = t in covered or t.startswith("delegate_to_")
    be = t in backend_by_tool
    if ui and be:
        status = "ok"
    elif ui:
        status = "ui only (static/generic)"
    elif be:
        status = "backend only"
    else:
        status = "MISSING — generic 'Executing X'"
    if status != "ok":
        print(f"  {n:3d}x  {t:<35} {status}")
