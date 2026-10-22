"""Exercise runner main with SDK test doubles and the real completion validator."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

RUNNER_DIR = Path(__file__).resolve().parents[2] / "docker/openhands"


class FakeConversation:
    status = "finished"
    text = "Task complete."
    runs = 0

    def __init__(self, **kwargs):
        self.state = NS(execution_status=self.status, events=[NS(
            source="agent", llm_message=NS(content=[NS(text=self.text)], tool_calls=None))])

    def send_message(self, *args):
        pass

    def run(self):
        type(self).runs += 1


class TestRunnerCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = ModuleType("openhands")
        tools = ModuleType("openhands.tools")
        sdk = ModuleType("openhands.sdk")
        for name in ("LLM", "Agent", "LocalWorkspace", "Message", "TextContent", "Tool"):
            setattr(sdk, name, lambda **kwargs: NS(**kwargs))
        sdk.Conversation = FakeConversation
        package.tools = tools
        package.sdk = sdk
        modules = {"openhands": package, "openhands.tools": tools, "openhands.sdk": sdk}
        with patch.dict(sys.modules, modules), patch.object(sys, "path", [str(RUNNER_DIR), *sys.path]):
            spec = importlib.util.spec_from_file_location("test_runner", RUNNER_DIR / "chad_headless_runner.py")
            cls.runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.runner)

    def setUp(self):
        FakeConversation.runs = 0
        FakeConversation.status = "finished"
        FakeConversation.text = "Task complete."

    def execute(self, mcp=False, extra_env=None):
        env = {"CHAD_TASK": "offline regression", "CHAD_TIMEOUT_S": "0"}
        env.update(extra_env or {})
        if mcp:
            env["CHAD_MCP_CONFIG"] = '{"mcpServers":{"example":{"url":"http://invalid"}}}'
        self.output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(self.runner, "_load_skills", lambda p: []), \
             contextlib.redirect_stdout(self.output):
            return self.runner.main()

    def test_unexecuted_lfm_call_fails_without_success_marker_or_mcp_retry(self):
        FakeConversation.text = "<|tool_call_start|>[terminal(command='pwd')]<|tool_call_end|>"
        with self.assertRaisesRegex(RuntimeError, "unexecuted_tool_call"):
            self.execute(mcp=True)
        self.assertEqual(FakeConversation.runs, 1)
        self.assertNotIn("CHAD_DISPATCH_DONE", self.output.getvalue())

    def test_iteration_error_does_not_report_success(self):
        FakeConversation.status = "error"
        with self.assertRaisesRegex(RuntimeError, "execution_status=error"):
            self.execute()
        self.assertNotIn("CHAD_DISPATCH_DONE", self.output.getvalue())

    def test_completed_report_still_succeeds(self):
        self.assertEqual(self.execute(), 0)
        self.assertIn("CHAD_DISPATCH_DONE", self.output.getvalue())

    def test_local_generation_timeout_reaches_llm(self):
        llm = self.runner.LLM
        for value, expected in (("1200", 1200), ("invalid", 900), ("0", 1)):
            with self.subTest(value=value), patch.object(self.runner, "LLM", wraps=llm) as factory:
                self.execute(extra_env={"CHAD_LLM_TIMEOUT_S": value})
                self.assertEqual(factory.call_args.kwargs["timeout"], expected)


if __name__ == "__main__":
    unittest.main()
