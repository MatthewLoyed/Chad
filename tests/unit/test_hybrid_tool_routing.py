"""A phone turn must not have Limb-partitioned tools proxied into a client that can't run them.

30 tools are marked `ToolPartition.LOCAL` (read_file, run_host_command, control_volume,
capture_screen, ...) and HybridToolExecutor proxies them to the connected Limb. Only the
desktop Limb owns a local tool registry — the browser/PWA client answers TOOL_REQUEST with
"Local tools are unavailable in the browser client".

So every one of those tools failed when asked from the phone, even though the Brain runs on
the exact machine they target and has all ~122 tools loaded. It has the hands; it was just
delegating them to something without any.

Routing now falls back to the Brain when the turn's initiating client can't service the
call. Desktop turns are deliberately unchanged — they still proxy to the desktop Limb,
which has the interactive session that Click/Type/capture_screen need.

Broader question (should the partition exist at all?): issue #715.
"""

import asyncio

import pytest

from src.core.orchestration.context import client_type_var
from src.core.tools.hybrid_executor import HybridToolExecutor
from src.core.tools.partition import TOOL_MANIFEST, ToolPartition


class _Executor:
    def __init__(self, name):
        self.name = name
        self.calls = []

    async def execute_tool_call(self, tool_call, active_model=None, context=None):
        self.calls.append(tool_call.get("name"))
        return {"success": True, "ran_on": self.name}

    def format_action_message(self, tool_name, params):
        return self.name


def _executor_pair():
    brain, limb = _Executor("brain"), _Executor("limb")
    return HybridToolExecutor(local_executor=brain, remote_executor=limb), brain, limb


def _a_local_tool() -> str:
    """A real tool from the manifest, so the test tracks the shipped partitioning."""
    for name, partition in TOOL_MANIFEST.items():
        if partition == ToolPartition.LOCAL:
            return name
    pytest.skip("no LOCAL-partitioned tools in the manifest")


def _a_cloud_tool() -> str:
    for name, partition in TOOL_MANIFEST.items():
        if partition == ToolPartition.CLOUD:
            return name
    pytest.skip("no CLOUD-partitioned tools in the manifest")


def _run(hybrid, tool_name, client_type):
    token = client_type_var.set(client_type)
    try:
        return asyncio.run(hybrid.execute_tool_call({"name": tool_name}))
    finally:
        client_type_var.reset(token)


def test_browser_turn_runs_limb_tools_in_the_brain():
    """The bug: a phone asking for read_file got 'Local tools are unavailable'."""
    hybrid, brain, limb = _executor_pair()

    result = _run(hybrid, _a_local_tool(), "browser")

    assert result["ran_on"] == "brain", "a browser turn must not be proxied a local tool"
    assert limb.calls == [], "nothing should reach the client that can't service it"


def test_desktop_turn_still_proxies_to_the_limb():
    """Unchanged on the daily driver: the desktop Limb has the interactive session that
    Click/Type/capture_screen need, so its turns keep going there."""
    hybrid, brain, limb = _executor_pair()

    result = _run(hybrid, _a_local_tool(), "desktop")

    assert result["ran_on"] == "limb"
    assert brain.calls == []


def test_autonomous_turn_keeps_the_old_behaviour():
    """No client started a cron/heartbeat turn. Leave its routing alone rather than
    silently moving where background work executes."""
    hybrid, _brain, limb = _executor_pair()

    token = client_type_var.set(None)
    try:
        result = asyncio.run(hybrid.execute_tool_call({"name": _a_local_tool()}))
    finally:
        client_type_var.reset(token)

    assert result["ran_on"] == "limb"


@pytest.mark.parametrize("client_type", ["browser", "desktop", None])
def test_cloud_tools_always_run_in_the_brain(client_type):
    """Routing for CLOUD-partitioned tools must not shift regardless of client."""
    hybrid, _brain, limb = _executor_pair()

    result = _run(hybrid, _a_cloud_tool(), client_type)

    assert result["ran_on"] == "brain"
    assert limb.calls == []


def test_action_message_comes_from_whichever_executor_runs_it():
    """Otherwise the 'what Chad is doing' line is written by the wrong side."""
    hybrid, _brain, _limb = _executor_pair()
    tool = _a_local_tool()

    token = client_type_var.set("browser")
    try:
        assert hybrid.format_action_message(tool, {}) == "brain"
    finally:
        client_type_var.reset(token)

    token = client_type_var.set("desktop")
    try:
        assert hybrid.format_action_message(tool, {}) == "limb"
    finally:
        client_type_var.reset(token)
