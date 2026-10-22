"""
System Utilities for Process Management
Handles singleton enforcement (lock file) and aggressive zombie process cleanup.
"""

import os
import sys
import psutil
import logging
import signal
import threading
import time
import atexit
from pathlib import Path
from src.shared.logger import setup_logger


# Setup logger using unified setup
logger = setup_logger("SystemUtils")


def kill_process_tree(pid: int, timeout: float = 2.0) -> None:
    """Terminate a process and all its descendants (Windows-safe)."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    children = proc.children(recursive=True)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass

    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        return

    gone, alive = psutil.wait_procs(children + [proc], timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def free_tcp_port(port: int) -> bool:
    """Find and terminate whatever process is LISTENing on `port` (Windows-safe).

    The Brain server's port is contended across independent dev checkouts/worktrees,
    each with its own cwd — a directory-relative lock file (see ProcessGuard) can't
    catch that, since the actual resource being fought over is the OS-level port, not
    a file. Call this right before binding so 'just run the server' always works
    instead of failing with 'address already in use' when an old session's server
    is still alive.
    """
    my_pid = os.getpid()
    killed_any = False
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError) as e:
        logger.warning(f"Cannot enumerate TCP connections to free port {port}: {e}")
        return False

    seen_pids = set()
    for conn in connections:
        if not conn.laddr or conn.laddr.port != port or conn.status != psutil.CONN_LISTEN:
            continue
        pid = conn.pid
        if not pid or pid == my_pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        try:
            proc = psutil.Process(pid)
            logger.warning(f"Port {port} held by PID {pid} ({proc.name()}). Terminating to claim it...")
            kill_process_tree(pid)
            killed_any = True
        except psutil.NoSuchProcess:
            continue
        except Exception as e:
            logger.error(f"Failed to free port {port} from PID {pid}: {e}")

    if killed_any:
        time.sleep(0.5)  # let Windows release the socket before we try to bind it
    return killed_any


class ProcessGuard:
    """
    Guardian of the Process.
    Enforces Singleton rule (One Ring) and manages aggressive shutdown to prevent zombies.
    """
    
    def __init__(self, lock_file_name="process.lock"):
        self.pid = os.getpid()
        self.lock_file = Path(lock_file_name).resolve()
        self._suicide_timer = None
    
    def enforce_singleton(self):
        """
        Ensure this is the only instance running.
        If a lock file exists, check if that PID is alive.
        If alive, KILL IT.
        Then write our own PID.
        """
        logger.info(f"Enforcing Singleton. My PID: {self.pid}")
        
        if self.lock_file.exists():
            try:
                old_pid = int(self.lock_file.read_text().strip())
                if old_pid == self.pid:
                    return # We already own it (restarted?)
                
                if psutil.pid_exists(old_pid):
                    logger.warning(f"Found existing instance (PID {old_pid}). Terminating it...")
                    try:
                        kill_process_tree(old_pid)
                        logger.info(f"Successfully neutralized zombie instance {old_pid}.")
                    except Exception as e:
                        logger.error(f"Failed to kill existing instance {old_pid}: {e}")
                else:
                    logger.info(f"Found stale lock file for PID {old_pid}. Cleaning up.")
                    
            except ValueError:
                logger.warning("Lock file contained invalid PID. overwrite.")
            except Exception as e:
                logger.error(f"Error checking lock file: {e}")
        
        # Claim the throne
        self._write_lock()
        
        # Register cleanup on exit
        atexit.register(self.cleanup)
        
    def _write_lock(self):
        try:
            self.lock_file.write_text(str(self.pid))
            logger.info(f"Lock file created at {self.lock_file}")
        except Exception as e:
            logger.error(f"Failed to write lock file: {e}")

    def cleanup(self):
        """Remove lock file on exit."""
        if self.lock_file.exists():
            try:
                # verify it's still us
                if int(self.lock_file.read_text().strip()) == self.pid:
                    self.lock_file.unlink()
                    logger.info("Lock file removed.")
            except:
                pass

    def register_signal_handlers(self, shutdown_callback):
        """
        Register signal handlers for SIGINT/SIGTERM.
        
        Args:
            shutdown_callback: Function to call when signal received (should initiate async shutdown)
        """
        def handler(signum, frame):
            logger.warning(f"Received signal {signum}. Initiating shutdown...")
            shutdown_callback()
            
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def hard_kill_self(self, delay=10.0):
        """
        Start a daemon thread that force kills the current process after delay.
        This is the 'Suicide Pact' to ensure we never hang even if threads are stuck.
        Call this when you begin your shutdown sequence.

        No-op under pytest. The only caller is the GUI shutdown path in
        app_controller.py. ``VoiceAssistantApp.__init__`` also registers
        ``_sync_shutdown`` via ``atexit``, so when the pytest process exits the
        atexit hook still runs — but ``PYTEST_CURRENT_TEST`` is already cleared
        by then. Guarding only on that env var therefore armed a real
        ExecutionerThread after a green suite and force-killed the runner
        ~10s later (non-zero exit, "Shutdown timed out" on a closed log stream).
        That matched the local Windows "suite dies for no reason" reports in
        #646; CI on Linux never collected ``test_app_controller.py`` so stayed green.

        Suppress whenever pytest is loaded OR a test is mid-flight OR
        ``CHAD_TESTING`` is set — never let the GUI suicide-pact shoot a harness.
        """
        if (
            "pytest" in sys.modules
            or "PYTEST_CURRENT_TEST" in os.environ
            or os.environ.get("CHAD_TESTING", "").strip().lower() in {"1", "true", "yes"}
        ):
            logger.debug("Executioner suppressed under pytest/CHAD_TESTING (would force-kill the harness).")
            return

        def _executioner():
            time.sleep(delay)
            # Before killing, try to remove the lock file so the NEXT run isn't blocked by a stale lock
            logger.critical(f"Shutdown timed out ({delay}s). Performing Seppuku (Force Kill).")
            self.cleanup()
            # Force kill self
            try:
                p = psutil.Process(self.pid)
                p.kill()
            except:
                os._exit(1) # Final fallback

        t = threading.Thread(target=_executioner, daemon=True, name="ExecutionerThread")
        t.start()
        logger.info(f"Executioner armed. Process will terminate in {delay}s if not closed cleanly.")

