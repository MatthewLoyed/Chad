"""
Computer-use safeguards for browser_agent's Playwright MCP tool calls (issue #279).

Posture: read-only by default, confirm writes. This module classifies each
`browser_*` MCP call as read-only or state-changing (a "write"), tracks the
per-conversation current-page domain and a hard per-conversation action-budget
kill-switch, and decides whether a call must pause for a human Approve/Decline
decision via the existing executor-agnostic `approval_gate` (the same
infrastructure that already backs ApprovalCard.tsx for send_email,
execute_sql, etc. — see docs/architecture/SESSION_ISOLATION.md).

Wired in from `MCPExecutor.execute()` (executor_strategy.py) for every
`tool_name.startswith("browser_")` call, before the loop-breaker check.
"""

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("BrowserSafety")

# Issue #328: user-selectable autonomy dial for browser_agent tool calls.
# - allow_all: no approval pauses (default while approval UX is being hardened)
# - model_decide: writes + stricter off-allowlist interactions (issue #279 behavior)
# - ask_all: every browser_* action pauses, including reads/navigate
VALID_BROWSER_APPROVAL_MODES = frozenset({"allow_all", "model_decide", "ask_all"})
DEFAULT_BROWSER_APPROVAL_MODE = "allow_all"

# Tools that always change page/account state regardless of arguments —
# uploading a file, accepting/dismissing a native dialog, choosing a <select>
# option, drag-and-drop, and multi-field form fills are never "just reading".
ALWAYS_WRITE_TOOLS = frozenset({
    "browser_file_upload",
    "browser_handle_dialog",
    "browser_select_option",
    "browser_drag",
    "browser_fill_form",
    "browser_evaluate",
})

# Tools that are pure observation/navigation and never require approval,
# regardless of the destination domain's trust level.
READ_ONLY_TOOLS = frozenset({
    "browser_navigate",
    "browser_navigate_back",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_console_messages",
    "browser_network_requests",
    "browser_tabs",
    "browser_resize",
    "browser_wait_for",
    "browser_close",
    "browser_install",
})

# Tools whose write/read status depends on their arguments or the accessible
# element description (checked against WRITE_SIGNAL_KEYWORDS below).
CONDITIONAL_TOOLS = frozenset({
    "browser_click",
    "browser_type",
    "browser_press_key",
})

# Signal words in an element's accessible name/description (the `element`
# param Playwright MCP passes for click/type calls) or in typed text that
# mark an action as state-changing. Case-insensitive substring match.
WRITE_SIGNAL_KEYWORDS = frozenset({
    "submit", "send", "sign up", "signup", "subscribe", "buy", "purchase",
    "checkout", "check out", "pay", "payment", "order", "confirm", "delete",
    "remove", "save", "post", "publish", "join", "register", "book",
    "add to cart", "place order", "donate", "transfer",
})

# Keys pressed that submit a form even though `browser_press_key` looks
# innocuous (e.g. Tab/PageDown are navigation, Enter typically submits).
WRITE_SIGNAL_KEYS = frozenset({"Enter", "NumpadEnter"})


def _element_description(params: Dict[str, Any]) -> str:
    parts = []
    for key in ("element", "text", "value"):
        val = params.get(key)
        if isinstance(val, str):
            parts.append(val)
    values = params.get("values")
    if isinstance(values, list):
        parts.extend(str(v) for v in values)
    fields = params.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                parts.append(str(field.get("name", "")))
                parts.append(str(field.get("value", "")))
    return " ".join(parts).lower()


def _matches_write_keywords(params: Dict[str, Any]) -> bool:
    description = _element_description(params)
    return any(keyword in description for keyword in WRITE_SIGNAL_KEYWORDS)


def is_write_action(tool_name: str, params: Dict[str, Any]) -> bool:
    """
    Classify a browser_* MCP call as a state-changing "write" (True) or a
    read-only/navigation action (False). Only meaningful for tool names that
    start with "browser_"; callers must guard that themselves.
    """
    if tool_name in ALWAYS_WRITE_TOOLS:
        return True
    if tool_name in READ_ONLY_TOOLS:
        return False
    if tool_name == "browser_press_key":
        return params.get("key") in WRITE_SIGNAL_KEYS
    if tool_name == "browser_type":
        if params.get("submit"):
            return True
        return _matches_write_keywords(params)
    if tool_name == "browser_click":
        return _matches_write_keywords(params)
    # Unknown/future browser_* tool: default to the safer "write" classification.
    return tool_name not in READ_ONLY_TOOLS


def extract_domain(url: Optional[str]) -> Optional[str]:
    """Best-effort registrable host from a URL, lowercased, no 'www.' prefix."""
    if not isinstance(url, str) or not url.strip():
        return None
    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    host = urlparse(candidate).hostname
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def get_browser_allowlist() -> list:
    return list(ConfigManager.get("tools.browser_write_allowlist", []) or [])


def get_browser_approval_mode() -> str:
    """Return the active browser approval mode (config + preferences merge)."""
    mode = ConfigManager.get("tools.browser_approval_mode", DEFAULT_BROWSER_APPROVAL_MODE)
    if mode not in VALID_BROWSER_APPROVAL_MODES:
        logger.warning(f"Unknown browser_approval_mode {mode!r}; falling back to allow_all")
        return DEFAULT_BROWSER_APPROVAL_MODE
    return mode


def get_browser_max_actions() -> int:
    return int(ConfigManager.get("tools.browser_max_actions_per_task", 60))


def is_domain_allowlisted(domain: Optional[str], allowlist: Optional[list] = None) -> bool:
    """True if `domain` is (or is a subdomain of) any entry in the allowlist."""
    if not domain:
        return False
    entries = allowlist if allowlist is not None else get_browser_allowlist()
    for entry in entries:
        entry_host = extract_domain(entry) or str(entry).lower().lstrip("www.")
        if domain == entry_host or domain.endswith(f".{entry_host}"):
            return True
    return False


class BrowserSafetyState:
    """
    Per-conversation state for the browser safeguards: the last-navigated
    domain (for allowlist checks) and a running action count (kill-switch).
    Lives on the single long-lived MCPExecutor instance, same pattern as its
    existing `_browser_call_failure_counts` loop-breaker dict — fine for this
    single-user, single-active-browser-session desktop app.
    """

    def __init__(self) -> None:
        self._current_domain: Dict[str, str] = {}
        self._current_url: Dict[str, str] = {}
        self._action_counts: Dict[str, int] = {}

    def note_navigation(self, conversation_id: Optional[str], url: Optional[str]) -> None:
        key = conversation_id or ""
        domain = extract_domain(url)
        if domain:
            self._current_domain[key] = domain
        if isinstance(url, str) and url.strip():
            self._current_url[key] = url.strip()

    def current_domain(self, conversation_id: Optional[str]) -> Optional[str]:
        return self._current_domain.get(conversation_id or "")

    def current_url(self, conversation_id: Optional[str]) -> Optional[str]:
        return self._current_url.get(conversation_id or "")

    def record_action(self, conversation_id: Optional[str]) -> int:
        """Increment and return this conversation's browser action count."""
        key = conversation_id or ""
        count = self._action_counts.get(key, 0) + 1
        self._action_counts[key] = count
        return count

    def budget_exceeded(self, conversation_id: Optional[str]) -> bool:
        count = self._action_counts.get(conversation_id or "", 0)
        return count > get_browser_max_actions()


def classify(
    tool_name: str,
    params: Dict[str, Any],
    *,
    current_domain: Optional[str],
) -> Dict[str, Any]:
    """
    Full classification for one browser_* call.

    Returns a dict: {
        "is_write": bool,
        "domain_allowlisted": bool,
        "requires_approval": bool,   # write, OR off-allowlist + interactive tool
        "reason": str,
    }
    """
    mode = get_browser_approval_mode()
    write = is_write_action(tool_name, params)
    allowlisted = is_domain_allowlisted(current_domain)

    if mode == "allow_all":
        return {
            "is_write": write,
            "domain_allowlisted": allowlisted,
            "requires_approval": False,
            "reason": "allow_all mode",
        }

    if mode == "ask_all":
        return {
            "is_write": write,
            "domain_allowlisted": allowlisted,
            "requires_approval": True,
            "reason": f"'{tool_name}' requires approval (ask_all mode)",
        }

    # model_decide — issue #279 default: read-only by default, confirm writes.
    # Off-allowlist "stricter treatment": any interactive action (not just
    # ones that already look like a write) requires approval once we know the
    # agent is on a specific, untrusted site, since we can't fully trust
    # element-description heuristics on a page we have no prior trust signal
    # for. Only applies once a domain has actually been observed (e.g. via
    # browser_navigate) — an unknown domain is not the same as "untrusted".
    off_list_interactive = (
        current_domain is not None and not allowlisted and tool_name in CONDITIONAL_TOOLS
    )

    requires_approval = write or off_list_interactive
    if write:
        reason = f"'{tool_name}' is a state-changing browser action"
    elif off_list_interactive:
        reason = f"'{tool_name}' targets an un-allowlisted site ({current_domain or 'unknown'})"
    else:
        reason = "read-only"

    return {
        "is_write": write,
        "domain_allowlisted": allowlisted,
        "requires_approval": requires_approval,
        "reason": reason,
    }
