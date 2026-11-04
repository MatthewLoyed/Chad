"""Dispatch and status expose durable traces without contacting Docker or models."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.tools.dev import openhands_dispatch as bridge


@pytest.mark.asyncio
async def test_dispatch_mounts_job_artifacts_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_cfg", lambda key, default: default)
    monkeypatch.setattr(bridge, "_build_mcp_config", lambda **kwargs: {})
    monkeypatch.setattr(bridge, "_search_env", lambda: [])
    monkeypatch.setattr(bridge, "preflight", AsyncMock(return_value={"checks": {}}))
    monkeypatch.setattr(bridge, "_prune_old_job_containers", AsyncMock())
    launch = AsyncMock(return_value=None)
    monkeypatch.setattr(bridge, "_start_coding_job", launch)
    result = await bridge.OpenHandsDispatchTool().execute(task="Build a fictional fixture")
    assert result["success"]
    job = result["result"]
    artifacts = Path(job["artifacts"])
    workspace = Path(job["workspace"])
    assert artifacts.is_dir()
    assert not artifacts.is_relative_to(workspace)
    argv = launch.call_args.args[0]
    assert f"{bridge._win_mount(artifacts)}:/chad-run" in argv
    assert "CHAD_RUN_ARTIFACTS=/chad-run" in argv
    assert "CHAD_COMPLETION_CONTRACT=/chad-contract.json" in argv
    assert any(arg.endswith(":/chad-contract.json:ro") for arg in argv)


@pytest.mark.asyncio
async def test_status_returns_same_artifact_path(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_run_docker", AsyncMock(side_effect=[(0, "running|0", ""), (0, "", "")]))
    result = await bridge.CheckCodingTaskTool().execute(job_id="example")
    assert result["result"]["artifacts"] == str(tmp_path / ".jobs/example/artifacts")


@pytest.mark.asyncio
async def test_exit_zero_without_verification_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_run_docker", AsyncMock(side_effect=[(0, "exited|0", ""), (0, "CHAD_DISPATCH_DONE", "")]))
    from src.core.intelligence import coding_residency
    monkeypatch.setattr(coding_residency, "finish", AsyncMock())
    result = await bridge.CheckCodingTaskTool().execute(job_id="example")
    assert result['result']['status'] == 'failed'
    assert result['result']['verification_status'] == 'unverified'


@pytest.mark.asyncio
async def test_status_invalidates_receipt_after_source_edit(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(bridge, "_workspace_root", lambda: tmp_path)
    ws = bridge._workspace_for('example'); ws.mkdir()
    source = ws / 'source.txt'; source.write_text('initial')
    bridge._save_job_workspace('example', ws)
    artifacts = bridge._run_artifacts('example'); artifacts.mkdir(parents=True)
    contract = artifacts.parent / 'contract.json'; contract.write_text('{"commands":[]}')
    import hashlib
    (artifacts / 'completion.json').write_text(json.dumps({
        'status': 'passed', 'fingerprint': bridge._completion_module().fingerprint(ws),
        'contract_hash': hashlib.sha256(contract.read_bytes()).hexdigest()}))
    from src.core.intelligence import coding_residency
    monkeypatch.setattr(coding_residency, "finish", AsyncMock())
    async def status():
        monkeypatch.setattr(bridge, "_run_docker", AsyncMock(side_effect=[(0, "exited|0", ""), (0, "CHAD_DISPATCH_DONE", "")]))
        return (await bridge.CheckCodingTaskTool().execute(job_id='example'))['result']
    assert (await status())['verification_status'] == 'verified_complete'
    source.write_text('changed after check')
    assert (await status())['status'] == 'failed'
