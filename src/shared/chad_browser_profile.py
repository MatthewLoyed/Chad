"""
ChadBrowser automation profile — single source of truth for Playwright.

All browser automation (Playwright MCP, Fara probe, seed script) MUST use this
dedicated profile. Never point --user-data-dir at the user's real Chrome
(Google\\Chrome\\User Data) — that locks daily browsing and was the source of
zombie MCP processes holding the wrong profile.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

logger = logging.getLogger("ChadBrowserProfile")

# Canonical automation profile (Playwright uses Default/ inside this dir).
CHAD_BROWSER_USER_DATA = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expandvars(r"%USERPROFILE%\AppData\Local"),
    "ChadBrowser",
    "UserData",
)

# Real Chrome profile roots we must never attach automation to.
_FORBIDDEN_USER_DATA_MARKERS = (
    r"\google\chrome\user data",
    r"/google/chrome/user data",
    r"\microsoft\edge\user data",
    r"/microsoft/edge/user data",
)


def resolve_chad_user_data_dir() -> str:
    """Absolute path to the ChadBrowser user-data dir (created if missing)."""
    path = os.path.normpath(os.path.expandvars(CHAD_BROWSER_USER_DATA))
    os.makedirs(path, exist_ok=True)
    return path


def is_forbidden_browser_profile(path: str) -> bool:
    """True if path is (or is inside) the user's real Chrome/Edge profile."""
    if not path:
        return False
    norm = os.path.normpath(os.path.expandvars(path)).lower()
    chad = resolve_chad_user_data_dir().lower()
    if norm == chad or norm.startswith(chad + os.sep):
        return False
    return any(marker in norm for marker in _FORBIDDEN_USER_DATA_MARKERS)


def extract_user_data_dir(command: Sequence[str]) -> Optional[str]:
    for i, arg in enumerate(command):
        if arg == "--user-data-dir" and i + 1 < len(command):
            return str(command[i + 1])
        if isinstance(arg, str) and arg.startswith("--user-data-dir="):
            return arg.split("=", 1)[1]
    return None


def enforce_playwright_command(command: Sequence[str], server_name: str = "playwright") -> List[str]:
    """
    Return a copy of `command` with --user-data-dir forced to ChadBrowser.

    Raises ValueError if the configured path points at a real Chrome/Edge profile
    (we rewrite instead of failing when the flag is present but wrong, and always
    inject ChadBrowser so a missing flag cannot fall through to Playwright defaults).
    """
    if server_name != "playwright":
        return list(command)

    chad = resolve_chad_user_data_dir()
    out: List[str] = []
    i = 0
    saw_flag = False
    while i < len(command):
        arg = command[i]
        if arg == "--user-data-dir" and i + 1 < len(command):
            configured = str(command[i + 1])
            expanded = os.path.normpath(os.path.expandvars(configured))
            if is_forbidden_browser_profile(expanded):
                logger.error(
                    "[ CHAD_BROWSER ] Refusing real-browser profile %r for Playwright; "
                    "forcing ChadBrowser at %s",
                    expanded,
                    chad,
                )
            elif os.path.normpath(expanded).lower() != chad.lower():
                logger.warning(
                    "[ CHAD_BROWSER ] Playwright user-data-dir was %r; forcing ChadBrowser at %s",
                    expanded,
                    chad,
                )
            out.extend(["--user-data-dir", chad])
            saw_flag = True
            i += 2
            continue
        if isinstance(arg, str) and arg.startswith("--user-data-dir="):
            configured = arg.split("=", 1)[1]
            expanded = os.path.normpath(os.path.expandvars(configured))
            if is_forbidden_browser_profile(expanded):
                logger.error(
                    "[ CHAD_BROWSER ] Refusing real-browser profile %r; forcing ChadBrowser",
                    expanded,
                )
            out.append(f"--user-data-dir={chad}")
            saw_flag = True
            i += 1
            continue
        out.append(arg)
        i += 1

    if not saw_flag:
        logger.warning(
            "[ CHAD_BROWSER ] Playwright command missing --user-data-dir; injecting %s",
            chad,
        )
        out.extend(["--user-data-dir", chad])

    return out


def pids_locking_profile(user_data_dir: Optional[str] = None) -> List[int]:
    """
    Root PIDs for Playwright MCP servers (and only those).

    kill_process_tree on these reaps the chrome children they spawned.
    We deliberately do NOT match a manually opened
    ``chrome.exe --user-data-dir=ChadBrowser`` window — that is a human session.
    Rogue MCP processes attached to the *real* Chrome profile are included so
    they cannot keep locking daily browsing.
    """
    try:
        import psutil
    except ImportError:
        return []

    # user_data_dir reserved for future filtering; MCP cmdline is the signal.
    _ = user_data_dir
    pids: List[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline_list = proc.info.get("cmdline") or []
            if not cmdline_list:
                continue
            cmdline = " ".join(cmdline_list).lower()
            # npx / node wrappers for @playwright/mcp
            if "@playwright/mcp" in cmdline or "playwright\\mcp\\cli.js" in cmdline.replace("/", "\\"):
                pids.append(proc.info["pid"])
        except (psutil.Error, TypeError, KeyError):
            continue
    return pids


def kill_profile_locks(
    user_data_dir: Optional[str] = None,
    *,
    extra_pids: Optional[Iterable[int]] = None,
) -> int:
    """
    Kill Playwright MCP process trees (node + the chrome they spawned).

    Returns the number of root PIDs we attempted to kill.
    Does not kill a manually opened ChadBrowser chrome window.
    Does kill rogue @playwright/mcp attached to the real Chrome profile.
    """
    from src.core.utils.system_utils import kill_process_tree

    targets = set(pids_locking_profile(user_data_dir))
    if extra_pids:
        targets.update(int(p) for p in extra_pids if p)

    killed = 0
    for pid in sorted(targets):
        try:
            kill_process_tree(pid)
            killed += 1
            logger.info("[ CHAD_BROWSER ] Killed playwright MCP tree pid=%s", pid)
        except Exception as e:
            logger.debug("[ CHAD_BROWSER ] Could not kill pid=%s: %s", pid, e)
    return killed


_SB_TOKEN_RE = re.compile(rb"sb-[a-z0-9]+-auth-token")


def list_supabase_auth_tokens(user_data_dir: Optional[str] = None) -> List[str]:
    """Scan Default/Local Storage/leveldb for sb-*-auth-token keys (best-effort)."""
    root = Path(user_data_dir or resolve_chad_user_data_dir())
    leveldb = root / "Default" / "Local Storage" / "leveldb"
    if not leveldb.is_dir():
        return []
    found: set[str] = set()
    for path in leveldb.iterdir():
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for match in _SB_TOKEN_RE.finditer(data):
            found.add(match.group().decode("ascii", errors="ignore"))
    return sorted(found)
