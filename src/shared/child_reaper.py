"""Crash-safe reaping of subprocesses we spawn (MCP stdio servers, mainly).

The problem this exists for (#675): the MCP fleet is spawned as stdio children and torn
down only by ``stdio_client``'s async context managers. Those run on a *graceful* exit.
They do not run when the process dies hard — an OOM kill, a segfault in a native
extension, ``taskkill``, a Ctrl-C that lands somewhere unfortunate. Every such death
leaves the whole fleet behind, and because nothing ever reaped them, repeated eval runs
stacked fleet on fleet: a real measurement found **42 orphaned node and 8 orphaned python
processes** on the box, at which point new runs were dying almost immediately.

Two layers, because neither alone is sufficient:

1. **A Windows Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.** Children assigned
   to the job are killed by the *kernel* when the last handle to the job closes — which
   happens when this process exits, however it exits. This is the only mechanism that
   survives a hard kill, and it is why this module is worth having at all.
2. **An ``atexit`` sweep** over registered PIDs. Redundant with the job on Windows, but it
   is the only layer on POSIX, and it gives a clean, logged teardown on a normal exit
   instead of relying on the kernel.

Everything here is best-effort and never raises into a caller: failing to reap a child is
strictly better than crashing the thing that spawned it.

Scoping note: this only ever touches PIDs handed to :func:`register_child` by this process.
It never scans for processes by name, so a dev-box eval run cannot reap the production
Brain's MCP fleet (a real hazard — see #675, where hand-cleanup had to walk PID trees
carefully for exactly this reason).
"""
from __future__ import annotations

import atexit
import os
import threading
from typing import Iterable, List, Set

from src.shared.logger import setup_logger

logger = setup_logger("ChildReaper")

_lock = threading.Lock()
_registered: Set[int] = set()
_atexit_installed = False

# Windows job handle, kept alive for the process lifetime on purpose: closing the last
# handle is what triggers the kill, so it must NOT be garbage-collected early. Guarded by
# its own lock (MCP servers connect concurrently, so registration is genuinely multi-
# threaded) — without it two threads can each create a job and children end up split
# across them.
_job_lock = threading.Lock()
_job_handle = None
_job_init_attempted = False


# ---------------------------------------------------------------------------
# Windows job object
# ---------------------------------------------------------------------------

def _kernel32():
    """kernel32 with explicit arg/restypes.

    ctypes defaults every restype to ``c_int``, which silently truncates a 64-bit
    ``HANDLE`` to 32 bits — the resulting handle is garbage and every later call on it
    fails for no visible reason. Declaring the signatures is not optional here.
    """
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


def _init_job() -> object | None:
    """Create (once) a kill-on-close job object. Returns the handle, or None."""
    global _job_handle, _job_init_attempted
    with _job_lock:
        return _init_job_locked()


def _init_job_locked() -> object | None:
    global _job_handle, _job_init_attempted
    if _job_init_attempted:
        return _job_handle
    _job_init_attempted = True
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = _kernel32()

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

        _job_handle = handle
        logger.debug("[ REAPER ] Kill-on-close job object created.")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[ REAPER ] Job object unavailable (non-fatal): {exc}")
        _job_handle = None
    return _job_handle


def _assign_to_job(pid: int) -> bool:
    """Put ``pid`` in the kill-on-close job. False if unavailable or it failed."""
    handle = _init_job()
    if not handle:
        return False
    try:
        kernel32 = _kernel32()
        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
        proc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if not proc:
            return False
        try:
            # Nested jobs are fine on Win8+: a child already inside another job (Claude
            # Code's own, a CI runner's) joins ours as well rather than failing.
            return bool(kernel32.AssignProcessToJobObject(handle, proc))
        finally:
            kernel32.CloseHandle(proc)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[ REAPER ] Could not assign pid={pid} to job: {exc}")
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_child(pid: int) -> None:
    """Track ``pid`` (and, on Windows, hand it to the kill-on-close job)."""
    if not pid or int(pid) == os.getpid():
        return
    pid = int(pid)
    with _lock:
        _install_atexit_locked()
        if pid in _registered:
            return
        _registered.add(pid)
    _assign_to_job(pid)


def register_children(pids: Iterable[int]) -> None:
    for pid in pids or ():
        register_child(pid)


def unregister_child(pid: int) -> None:
    """Stop tracking ``pid`` — it was reaped by its owner's own teardown.

    Only removes it from the atexit sweep; a process cannot be pulled back out of a
    Windows job object, which is fine: the job only kills what is still alive.
    """
    with _lock:
        _registered.discard(int(pid))


def snapshot_own_children() -> List[int]:
    """PIDs of this process's direct children, right now.

    Used to diff around a spawn when the spawning API doesn't hand back a pid — which is
    the case for the MCP SDK's ``stdio_client``, whose transport owns the process object
    internally and exposes only the read/write streams.
    """
    try:
        import psutil

        return [c.pid for c in psutil.Process(os.getpid()).children(recursive=False)]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[ REAPER ] Could not snapshot children: {exc}")
        return []


def reap_all(reason: str = "exit") -> int:
    """Kill every registered child tree. Returns how many roots were killed."""
    with _lock:
        pids = sorted(_registered)
        _registered.clear()
    if not pids:
        return 0

    from src.core.utils.system_utils import kill_process_tree

    killed = 0
    for pid in pids:
        try:
            import psutil

            if not psutil.pid_exists(pid):
                continue
            kill_process_tree(pid)
            killed += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[ REAPER ] Could not reap pid={pid}: {exc}")
    if killed:
        logger.info(f"[ REAPER ] Reaped {killed} child process tree(s) on {reason}.")
    return killed


def registered_pids() -> List[int]:
    with _lock:
        return sorted(_registered)


def _install_atexit_locked() -> None:
    global _atexit_installed
    if _atexit_installed:
        return
    atexit.register(reap_all, "atexit")
    _atexit_installed = True


__all__ = [
    "register_child",
    "register_children",
    "unregister_child",
    "snapshot_own_children",
    "reap_all",
    "registered_pids",
]
