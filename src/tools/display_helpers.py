"""Shared helpers for human-readable tool action labels (ToolFormatter / tool-ui).

When adding a tool, either call these from format_action() or implement
format_action inline on the tool class. Keep wording in sync with
src/clients/web/src/utils/tool-ui.tsx.
"""

from typing import Any, Dict, Optional


def truncate_label(text: str, max_len: int = 40) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_drive_search(params: Dict[str, Any]) -> str:
    query = truncate_label(str(params.get("query") or ""))
    if not query:
        return "Searching Google Drive"
    file_type = (params.get("file_type") or "all").lower()
    if file_type and file_type != "all":
        return f'Searching Drive for "{query}" ({file_type})'
    return f'Searching Drive for "{query}"'


def format_drive_list_folder(params: Dict[str, Any]) -> str:
    folder_id = params.get("folder_id") or "root"
    if folder_id == "root":
        return "Listing My Drive"
    return f"Listing Drive folder {truncate_label(str(folder_id), 24)}"


def format_drive_read(params: Dict[str, Any], file_name: Optional[str] = None) -> str:
    name = file_name or params.get("name") or params.get("file_name")
    if name:
        return f'Reading "{truncate_label(str(name))}" from Drive'
    return "Reading file from Drive"


def format_drive_create_doc(params: Dict[str, Any]) -> str:
    title = truncate_label(str(params.get("title") or ""))
    if title:
        return f'Creating Google Doc "{title}"'
    return "Creating Google Doc"


def format_drive_edit_doc(params: Dict[str, Any], doc_name: Optional[str] = None) -> str:
    label = doc_name or params.get("title")
    target = f'"{truncate_label(str(label))}"' if label else "Google Doc"
    if params.get("append_text"):
        return f"Appending to {target}"
    if params.get("find_text"):
        return f"Editing {target}"
    if params.get("replace_all_content"):
        return f"Replacing content in {target}"
    return f"Editing {target}"


def format_sandbox_python(params: Dict[str, Any]) -> str:
    code = (params.get("code") or "").strip()
    if not code:
        return "Running Python code"
    first_line = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
    snippet = truncate_label(first_line, 36)
    return f"Running Python: {snippet}" if snippet else "Running Python code"


def format_sandbox_bash(params: Dict[str, Any]) -> str:
    command = truncate_label(str(params.get("command") or ""), 48)
    return f"Running {command}" if command else "Running shell command"


def format_run_script(params: Dict[str, Any]) -> str:
    name = truncate_label(str(params.get("script_name") or ""))
    return f"Running script {name}" if name else "Running script"


def format_host_command(params: Dict[str, Any]) -> str:
    command = truncate_label(str(params.get("command") or ""), 48)
    return f"Running {command}" if command else "Running command"


def format_switch_to_plan_mode(params: Dict[str, Any]) -> str:
    reasoning = truncate_label(str(params.get("reasoning") or ""), 50)
    return f"Switching to plan mode: {reasoning}" if reasoning else "Switching to plan mode"


def format_get_datetime(params: Dict[str, Any]) -> str:
    fmt = (params.get("format") or "datetime").lower()
    if fmt == "time":
        return "Checking current time"
    if fmt == "date":
        return "Checking today's date"
    if fmt == "timestamp":
        return "Getting timestamp"
    return "Checking date and time"
