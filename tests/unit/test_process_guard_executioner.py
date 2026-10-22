"""Regression: the shutdown executioner must never fire against the test runner.

`ProcessGuard.hard_kill_self()` arms a daemon thread that force-kills its OWN process
after `delay` seconds. Its only production caller is the GUI shutdown path in
`app_controller.py` — which `tests/unit/test_app_controller.py` imports and exercises.
`VoiceAssistantApp.__init__` also registers `_sync_shutdown` via `atexit`, so when the
pytest process exits the hook still runs — but `PYTEST_CURRENT_TEST` is already cleared.
Guarding only on that env var therefore armed a real self-destruct after a green suite
(#646): non-zero exit, `Shutdown timed out` logged against a closed stream.

It stayed hidden in CI because `tests/unit/conftest.py` skips the Windows-only
app_controller tests on Linux.
"""

import os
import sys
import threading
from unittest.mock import patch

from src.core.utils.system_utils import ProcessGuard


def test_hard_kill_self_is_suppressed_under_pytest():
    """Arming the executioner from a test must be a no-op, not a live timer."""
    assert "PYTEST_CURRENT_TEST" in os.environ, "expected pytest to mark the running test"

    before = {t.name for t in threading.enumerate()}
    ProcessGuard(lock_file_name="test_executioner.lock").hard_kill_self(delay=0.5)
    spawned = {t.name for t in threading.enumerate()} - before

    assert not any("Executioner" in name for name in spawned), (
        f"hard_kill_self armed a real executioner under pytest (new threads: {spawned}) — "
        "it would force-kill the test runner"
    )


def test_hard_kill_self_suppressed_when_pytest_loaded_but_current_test_cleared():
    """atexit after the suite: PYTEST_CURRENT_TEST is gone, pytest is still in sys.modules."""
    assert "pytest" in sys.modules
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        os.environ.pop("CHAD_TESTING", None)
        before = {t.name for t in threading.enumerate()}
        ProcessGuard(lock_file_name="test_executioner_atexit.lock").hard_kill_self(delay=0.5)
        spawned = {t.name for t in threading.enumerate()} - before

    assert not any("Executioner" in name for name in spawned), (
        f"hard_kill_self armed after PYTEST_CURRENT_TEST cleared (new threads: {spawned}) — "
        "atexit→_sync_shutdown would still shoot the runner (#646)"
    )


def test_no_executioner_thread_is_alive_during_the_suite():
    """Belt and braces: nothing else may leave a live executioner running either."""
    alive = [t.name for t in threading.enumerate() if "Executioner" in t.name]
    assert not alive, f"a shutdown executioner is armed against this pytest process: {alive}"
