"""Per-model native mode reaches the real Docker dispatch arguments."""
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from src.tools.dev import openhands_dispatch as dispatch


class TestModelToolMode(unittest.IsolatedAsyncioTestCase):
    async def check_mode(self, model, mapping, expected):
        config = {"openhands_coder_model": model, "openhands_native_tool_calling": False,
                  "openhands_native_tool_calling_by_model": mapping}
        def cfg(key, default):
            return config.get(key, default)
        docker = AsyncMock(return_value=(0, "container-id", ""))
        with tempfile.TemporaryDirectory() as workspace, \
             patch.object(dispatch, "_cfg", cfg), \
             patch.object(dispatch, "preflight", AsyncMock(return_value={"checks": {}})), \
             patch.object(dispatch, "_prune_old_job_containers", AsyncMock()), \
             patch.object(dispatch, "_ensure_coder_model", AsyncMock()), \
             patch.object(dispatch, "_build_mcp_config", return_value=None), \
             patch.object(dispatch, "_search_env", return_value=[]), \
             patch.object(dispatch, "_save_job_workspace"), \
             patch.object(dispatch, "_run_docker", docker), \
             patch.dict("os.environ", {"CHAD_EVAL_SUITE": ""}):
            result = await dispatch.OpenHandsDispatchTool().execute(task="Offline test", workspace=workspace)
        self.assertTrue(result["success"], result)
        self.assertIn("CHAD_NATIVE_TOOL_CALLING=" + str(int(expected)), docker.call_args.args[0])
        self.assertIn("CHAD_LLM_TIMEOUT_S=900", docker.call_args.args[0])

    async def test_lfm_uses_native_override(self):
        await self.check_mode("openai/lfm2.5-24b-a2b", {"openai/lfm2.5-24b-a2b": True}, True)

    async def test_qwen_keeps_text_baseline(self):
        await self.check_mode("openai/qwen/qwen3.6-35b-a3b", {"openai/lfm2.5-24b-a2b": True}, False)

    async def test_explicit_false_override_is_respected(self):
        await self.check_mode("openai/lfm2.5-24b-a2b", {"openai/lfm2.5-24b-a2b": False}, False)


if __name__ == "__main__":
    unittest.main()
