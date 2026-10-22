"""Offline termination regression tests; no Docker, SDK, or model required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "chad_run_validation", ROOT / "docker/openhands/chad_run_validation.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def message(text, source="agent"):
    return NS(source=source, llm_message=NS(content=[NS(text=text)], tool_calls=None))


def conversation(events, status="finished"):
    return NS(state=NS(execution_status=NS(value=status), events=events))


class TestTermination(unittest.TestCase):
    def test_lfm_text_call_is_not_completion(self):
        with self.assertRaisesRegex(RuntimeError, "unexecuted_tool_call"):
            validation.validate_conversation(conversation([
                message("<|tool_call_start|>[terminal(command='pwd')]<|tool_call_end|>")]))

    def test_hermes_text_call_is_not_completion(self):
        with self.assertRaisesRegex(RuntimeError, "unexecuted_tool_call"):
            validation.validate_conversation(conversation([message("<function=terminal>")]))

    def test_native_unexecuted_call_is_not_completion(self):
        event = message("")
        event.llm_message.tool_calls = [NS(id="pending")]
        with self.assertRaisesRegex(RuntimeError, "unexecuted_tool_call"):
            validation.validate_conversation(conversation([event]))

    def test_nonfinished_statuses_fail(self):
        for status in ("error", "stuck", "paused", "running", "waiting_for_confirmation"):
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, status):
                validation.validate_conversation(conversation([message("Done")], status))

    def test_no_agent_event_fails(self):
        with self.assertRaisesRegex(RuntimeError, "no final agent event"):
            validation.validate_conversation(conversation([message("Task", "user")]))

    def test_plain_final_message_allowed(self):
        validation.validate_conversation(conversation([message("Read-only report complete.")]))

    def test_empty_final_message_fails(self):
        with self.assertRaisesRegex(RuntimeError, "empty final agent message"):
            validation.validate_conversation(conversation([message("")]))

    def test_finish_action_allowed(self):
        validation.validate_conversation(conversation([NS(source="agent", action=NS(kind="FinishAction"))]))

    def test_earlier_call_does_not_poison_valid_finish(self):
        validation.validate_conversation(conversation([
            message("<|tool_call_start|>[terminal()]"), message("Done; checks passed.")]))

    def test_quoted_documentation_is_not_a_pending_call(self):
        validation.validate_conversation(conversation([message("The syntax is `<tool_call>`. It failed.")]))


if __name__ == "__main__":
    unittest.main()
