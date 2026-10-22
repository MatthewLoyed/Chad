"""MCPRegistry teardown — the fleet must be disconnectable and reapable (#675).

Before this, nothing in the codebase called `MCPServerEntry.disconnect()` at all: servers
were spawned at warmup and then abandoned, on clean exits as much as crashes. A 15-case
eval lane leaked ~12 node processes per run and consecutive runs stacked fleets.

These drive the registry directly rather than spawning real npx servers — the real-process
side is covered by tests/unit/test_child_reaper.py.
"""
import pytest

from src.core.tools.mcp_registry import MCPRegistry, MCPServerEntry


class _FakeEntry:
    def __init__(self, name, connected=True, raises=False):
        self.name = name
        self.session = object() if connected else None
        self._raises = raises
        self.disconnected = False

    async def disconnect(self):
        if self._raises:
            raise RuntimeError("stdio teardown wedged")
        self.disconnected = True
        self.session = None


async def test_disconnect_all_disconnects_only_connected_servers():
    registry = MCPRegistry()
    live, idle = _FakeEntry("github"), _FakeEntry("notion", connected=False)
    registry._servers = {"github": live, "notion": idle}

    assert await registry.disconnect_all() == 1
    assert live.disconnected is True
    assert idle.disconnected is False


async def test_one_wedged_server_does_not_strand_the_rest():
    """Best-effort per server: the whole point is that a crash leaves nothing behind."""
    registry = MCPRegistry()
    wedged, healthy = _FakeEntry("playwright", raises=True), _FakeEntry("github")
    registry._servers = {"playwright": wedged, "github": healthy}

    assert await registry.disconnect_all() == 2
    assert healthy.disconnected is True


async def test_disconnect_all_kills_in_flight_prewarm_processes():
    """A run that ends mid-`npx` package resolve leaves that spawn behind too."""
    class _Proc:
        def __init__(self, returncode):
            self.returncode = returncode
            self.killed = False

        def kill(self):
            self.killed = True

    registry = MCPRegistry()
    running, finished = _Proc(None), _Proc(0)
    registry._prewarm_procs = [running, finished]

    await registry.disconnect_all()

    assert running.killed is True
    assert finished.killed is False
    assert registry._prewarm_procs == []


def test_new_children_are_registered_with_the_reaper(monkeypatch):
    """Every server's child gets reaper coverage — not just playwright's, as before.

    Registration is on the whole diff, deliberately: at exit every MCP child must go,
    and over-registering is harmless while missing one is the bug.
    """
    registered = []
    monkeypatch.setattr("src.core.tools.mcp_registry.psutil", None)  # skip attribution
    monkeypatch.setattr("src.core.tools.mcp_registry.register_children", registered.extend)
    monkeypatch.setattr(
        "src.core.tools.mcp_registry.snapshot_own_children", lambda: [100, 200, 300]
    )

    entry = MCPServerEntry("github", ["npx", "-y", "@modelcontextprotocol/server-github"])
    entry._snapshot_managed_pids(pids_before={100})

    assert entry._managed_pids == [200, 300]
    assert registered == [200, 300]


def test_force_kill_reaps_tracked_children_of_a_non_playwright_server(monkeypatch):
    killed = []
    unregistered = []
    monkeypatch.setattr(
        "src.core.utils.system_utils.kill_process_tree", lambda pid, timeout=2.0: killed.append(pid)
    )
    monkeypatch.setattr("src.core.tools.mcp_registry.unregister_child", unregistered.append)
    monkeypatch.setattr("src.core.tools.mcp_registry.psutil", None)

    entry = MCPServerEntry("github", ["npx", "-y", "server"])
    entry._managed_pids = [4242, 4243]
    entry._force_kill_children()

    assert killed == [4242, 4243]
    assert unregistered == [4242, 4243]
    assert entry._managed_pids == []


def test_a_siblings_child_is_not_claimed_by_the_wrong_server(monkeypatch):
    """Servers connect concurrently, so a plain PID diff cross-talks between entries.

    Without cmdline attribution, disconnecting one server would kill another's process.
    """
    class _FakeProc:
        def __init__(self, pid, cmdline):
            self.pid, self._cmdline = pid, cmdline

        def cmdline(self):
            return self._cmdline

    procs = {
        700: _FakeProc(700, ["node", "@modelcontextprotocol/server-github/index.js"]),
        800: _FakeProc(800, ["node", "@notionhq/notion-mcp-server/index.js"]),
    }

    class _FakePsutil:
        Error = Exception

        @staticmethod
        def Process(pid):
            return procs[pid]

    registered = []
    monkeypatch.setattr("src.core.tools.mcp_registry.psutil", _FakePsutil)
    monkeypatch.setattr("src.core.tools.mcp_registry.register_children", registered.extend)
    monkeypatch.setattr(
        "src.core.tools.mcp_registry.snapshot_own_children", lambda: [700, 800]
    )

    entry = MCPServerEntry("github", ["npx", "-y", "@modelcontextprotocol/server-github"])
    entry._snapshot_managed_pids(pids_before=set())

    # Only ours is owned for targeted teardown...
    assert entry._managed_pids == [700]
    # ...but both are reaper-registered, because at exit every MCP child must go.
    assert sorted(registered) == [700, 800]
