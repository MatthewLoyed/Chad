"""child_reaper — crash-safe teardown of subprocesses we spawn (#675).

The bug this guards: MCP stdio children were torn down only by `stdio_client`'s async
context managers, which don't run on a hard death. Runs leaked their whole fleet and
consecutive runs stacked fleets (42 stray node processes were measured on the box).

The kill-on-job-close assertion here spawns a REAL child and kills its REAL parent with
`os._exit`, because that is the only thing that actually proves the mechanism — a mocked
`AssignProcessToJobObject` would pass while the kernel did nothing.
"""
import os
import subprocess
import sys
import textwrap
import time

import pytest

from src.shared import child_reaper


@pytest.fixture(autouse=True)
def _clean_registry():
    for pid in child_reaper.registered_pids():
        child_reaper.unregister_child(pid)
    yield
    for pid in child_reaper.registered_pids():
        child_reaper.unregister_child(pid)


def test_register_tracks_pids_and_ignores_self():
    child_reaper.register_child(999_001)
    child_reaper.register_child(999_002)
    child_reaper.register_child(os.getpid())  # never reap ourselves
    child_reaper.register_child(0)            # falsy pid is a no-op

    assert child_reaper.registered_pids() == [999_001, 999_002]


def test_unregister_removes_from_the_sweep():
    child_reaper.register_child(999_003)
    child_reaper.unregister_child(999_003)
    assert 999_003 not in child_reaper.registered_pids()


def test_reap_all_clears_the_registry_and_skips_dead_pids():
    # High PIDs that don't exist: reap_all must not raise, and must still drain.
    child_reaper.register_child(999_004)
    assert child_reaper.reap_all("test") == 0
    assert child_reaper.registered_pids() == []


def test_snapshot_own_children_sees_a_real_child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert proc.pid in child_reaper.snapshot_own_children()
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_reap_all_kills_a_real_child_tree():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    child_reaper.register_child(proc.pid)

    assert child_reaper.reap_all("test") == 1
    assert proc.wait(timeout=10) is not None


_HARD_DEATH_PARENT = textwrap.dedent("""
    import os, subprocess, sys, time
    sys.path.insert(0, sys.argv[2])
    from src.shared.child_reaper import register_child
    # DEVNULL, not a pipe: a surviving child would inherit the pipe and block the
    # parent's reader forever, which reads as a hang rather than a clean assertion.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.argv[1] == "register":
        register_child(child.pid)
    open(sys.argv[3], "w").write(str(child.pid))
    time.sleep(0.4)
    os._exit(1)   # hard death: no atexit, no finally, no context-manager exit
""")


def _spawn_hard_dying_parent(tmp_path, mode: str) -> int:
    script = tmp_path / "hard_death_parent.py"
    script.write_text(_HARD_DEATH_PARENT, encoding="utf-8")
    pid_file = tmp_path / f"{mode}.pid"
    subprocess.run(
        [sys.executable, str(script), mode, os.getcwd(), str(pid_file)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
    )
    return int(pid_file.read_text())


def _survives(pid: int, seconds: float = 4.0) -> bool:
    import psutil
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return False
        time.sleep(0.2)
    return True


@pytest.mark.skipif(os.name != "nt", reason="job objects are a Windows mechanism")
def test_job_object_kills_a_registered_child_when_the_parent_dies_hard(tmp_path):
    """The crash case: parent exits via os._exit, so no atexit and no finally run.

    Only the kernel-level kill-on-job-close can reap the child here — which is exactly
    the scenario that left orphans behind in #675.
    """
    pid = _spawn_hard_dying_parent(tmp_path, "register")
    try:
        assert not _survives(pid), (
            f"orphan {pid} survived its parent's hard death — the job object did not fire"
        )
    finally:
        _force_kill(pid)


@pytest.mark.skipif(os.name != "nt", reason="job objects are a Windows mechanism")
def test_an_unregistered_child_survives_the_same_hard_death(tmp_path):
    """Negative control, and the reason the test above means anything.

    Without it, "the child died" is equally consistent with the test runner's own job
    object reaping the tree, in which case the assertion above would pass on a completely
    broken reaper.
    """
    pid = _spawn_hard_dying_parent(tmp_path, "plain")
    try:
        assert _survives(pid), (
            f"unregistered child {pid} was reaped anyway — something other than "
            f"child_reaper is killing orphans, so the positive test proves nothing"
        )
    finally:
        _force_kill(pid)


def _force_kill(pid: int) -> None:
    import psutil
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass
