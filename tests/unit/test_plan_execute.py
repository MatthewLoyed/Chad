import os
import json
import pytest
import asyncio
from typing import Dict, Any, List, Optional
from src.core.orchestration.langgraph_engine import LangGraphEngine, ChadState
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


class MockToolRegistry:
    """
    Minimal tool registry that exposes complete_step and fail_step as real
    LangChain StructuredTools so the ToolNode can execute them in tests.
    """

    def __init__(self):
        from src.tools.system.complete_step import CompleteStepTool
        from src.tools.system.fail_step import FailStepTool
        self._step_tools = {
            "complete_step": CompleteStepTool(),
            "fail_step": FailStepTool(),
        }
        self._lc_cache: Dict[str, Any] = {}

    def _wrap(self, name: str, tool_instance: Any) -> Any:
        if name in self._lc_cache:
            return self._lc_cache[name]

        from langchain_core.tools import StructuredTool
        from pydantic import create_model, Field

        schema = tool_instance.get_schema()
        params = schema.get("parameters", {}).get("properties", {})
        required_params = schema.get("parameters", {}).get("required", [])

        TYPE_MAP = {
            "string": str, "integer": int, "boolean": bool,
            "number": float, "array": list, "object": dict,
        }
        fields = {
            p: (
                TYPE_MAP.get(info.get("type", "string"), str),
                Field(
                    default=... if p in required_params else None,
                    description=info.get("description", ""),
                ),
            )
            for p, info in params.items()
        }
        args_model = create_model(f"{name}_args", **fields)

        _tool = tool_instance

        async def _execute(**kwargs: Any) -> str:
            result = await _tool.execute(**kwargs)
            return json.dumps(result)

        lc_tool = StructuredTool.from_function(
            name=name,
            description=schema.get("description", ""),
            func=None,
            coroutine=_execute,
            args_schema=args_model,
        )
        self._lc_cache[name] = lc_tool
        return lc_tool

    def get_langchain_tool_by_name(self, name: str) -> Optional[Any]:
        tool = self._step_tools.get(name)
        return self._wrap(name, tool) if tool else None

    def get_langchain_tools_by_domain(self, domain: str) -> List[Any]:
        return []

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        return [tool.get_schema() for tool in self._step_tools.values()]

    def get_all_langchain_tools(self) -> List[Any]:
        return [self._wrap(name, t) for name, t in self._step_tools.items()]


class PlanExecuteMockAdapter(AIAdapter):
    def __init__(self):
        self.step_1_failed_once = False

    async def generate(self, messages, tools=None, **kwargs):
        system_prompt = ""
        for m in messages:
            if m.get("role") == "system":
                system_prompt = m.get("content", "")

        last_user_content = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_content = m.get("content", "")
                break

        try:
            print(f"\n[MOCK ADAPTER] Prompt System Header Sample: {system_prompt[:150]}")
        except UnicodeEncodeError:
            print(f"\n[MOCK ADAPTER] Prompt System Header Sample: {system_prompt[:150].encode('ascii', errors='replace').decode('ascii')}")
        try:
            print(f"[MOCK ADAPTER] Last User Content: {last_user_content}")
        except UnicodeEncodeError:
            print(f"[MOCK ADAPTER] Last User Content: {last_user_content.encode('ascii', errors='replace').decode('ascii')}")

        # 1. PLANNER STAGE — submit via propose_plan tool (planner no longer scrapes [[PLAN]] JSON)
        if "You are Chad's Planner" in system_prompt:
            if "scout_decision_required=True" in system_prompt:
                return GenerateResponse(
                    text="", tool_calls=[{
                        "id": "call_skip_scout", "name": "skip_scout",
                        "args": {"reason": "Calculation script inputs are supplied; no external facts needed."},
                    }], finish_reason="tool_calls",
                )
            if "RE-PLANNING REQUIRED due to task failure" in system_prompt:
                print("[MOCK ADAPTER] Generating RE-PLAN because step_1 failed.")
                return GenerateResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_propose_retry",
                        "name": "propose_plan",
                        "args": {
                            "steps": [{
                                "id": "step_1_retry",
                                "description": "Search standard templates and re-write script",
                                "tools": ["delegate_to_code_agent"],
                                "status": "pending",
                            }],
                        },
                    }],
                    finish_reason="tool_calls",
                )

            if "RE-PLANNING REQUIRED due to validation critique" in system_prompt:
                print("[MOCK ADAPTER] Generating RE-PLAN because validation failed.")
                return GenerateResponse(
                    text="",
                    tool_calls=[{
                        "id": "call_propose_validation_fix",
                        "name": "propose_plan",
                        "args": {
                            "steps": [{
                                "id": "step_validation_fix",
                                "description": "Fix script output files",
                                "tools": ["delegate_to_code_agent"],
                                "status": "pending",
                            }],
                        },
                    }],
                    finish_reason="tool_calls",
                )

            print("[MOCK ADAPTER] Generating initial plan.")
            return GenerateResponse(
                text="",
                tool_calls=[{
                    "id": "call_propose_initial",
                    "name": "propose_plan",
                    "args": {
                        "steps": [{
                            "id": "step_1",
                            "description": "Write a python script to run some calculations",
                            "tools": ["delegate_to_code_agent"],
                            "status": "pending",
                        }],
                    },
                }],
                finish_reason="tool_calls",
            )

        # 2. SUPERVISOR STAGE
        elif "ORCHESTRATION & ROUTING" in system_prompt:
            last_tool_output = ""
            for m in reversed(messages):
                content = ""
                role = ""
                if isinstance(m, dict):
                    content = m.get("content", "")
                    role = m.get("role", "")
                else:
                    content = getattr(m, "content", "")
                    role = getattr(m, "role", None)
                    if not role:
                        role = type(m).__name__

                if "tool" in str(role).lower() or "tool" in str(type(m).__name__).lower():
                    last_tool_output = str(content)
                    break

            # Plan/step status now rides in the trailing user turn (kept out of the
            # system prompt for KV-cache stability — see supervisor_node), so match
            # step state against the combined system + last-user view.
            plan_view = f"{system_prompt}\n{last_user_content}"

            # IMPORTANT: Check most-specific step IDs first to avoid substring false-positives.
            # e.g. "step_1" matches inside "step_1_retry", so step_1_retry must be checked first.

            # Handle step_validation_fix
            if "step_validation_fix" in plan_view and ("PENDING" in plan_view or "RUNNING" in plan_view):
                if "TASK_COMPLETED" in last_tool_output:
                    print("[MOCK ADAPTER] Supervisor calling complete_step for step_validation_fix.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_complete_validation_fix",
                            "name": "complete_step",
                            "args": {
                                "step_id": "step_validation_fix",
                                "summary": "Output files corrected successfully.",
                            },
                        }],
                        finish_reason="tool_calls",
                    )
                else:
                    print("[MOCK ADAPTER] Supervisor delegating validation fix to code_agent.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_delegate_code_fix",
                            "name": "delegate_to_code_agent",
                            "args": {"task": "Fix the output files"},
                        }],
                        finish_reason="tool_calls",
                    )

            # Handle step_1_retry (must come before step_1 check)
            elif "step_1_retry" in plan_view and ("PENDING" in plan_view or "RUNNING" in plan_view):
                if "TASK_COMPLETED" in last_tool_output:
                    print("[MOCK ADAPTER] Supervisor calling complete_step for step_1_retry.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_complete_step_retry",
                            "name": "complete_step",
                            "args": {
                                "step_id": "step_1_retry",
                                "summary": "Calculation script written successfully on retry.",
                            },
                        }],
                        finish_reason="tool_calls",
                    )
                else:
                    print("[MOCK ADAPTER] Supervisor delegating step_1_retry to code_agent.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_delegate_code_retry",
                            "name": "delegate_to_code_agent",
                            "args": {"task": "Retry writing calculation script"},
                        }],
                        finish_reason="tool_calls",
                    )

            # Handle step_1 — explicit guard: only when step_1_retry is NOT present
            elif (
                "step_1" in plan_view
                and "step_1_retry" not in plan_view
                and ("PENDING" in plan_view or "RUNNING" in plan_view)
            ):
                if "CRITICAL_FAILURE" in last_tool_output:
                    print("[MOCK ADAPTER] Supervisor calling fail_step for step_1.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_fail_step_1",
                            "name": "fail_step",
                            "args": {
                                "step_id": "step_1",
                                "reason": "Command failed with syntax error. Script could not be generated.",
                            },
                        }],
                        finish_reason="tool_calls",
                    )
                elif "TASK_COMPLETED" in last_tool_output:
                    print("[MOCK ADAPTER] Supervisor calling complete_step for step_1.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_complete_step_1",
                            "name": "complete_step",
                            "args": {
                                "step_id": "step_1",
                                "summary": "Calculation script generated.",
                            },
                        }],
                        finish_reason="tool_calls",
                    )
                else:
                    print("[MOCK ADAPTER] Supervisor delegating step_1 to code_agent.")
                    return GenerateResponse(
                        text="",
                        tool_calls=[{
                            "id": "call_delegate_code",
                            "name": "delegate_to_code_agent",
                            "args": {"task": "Write the calculation script"},
                        }],
                        finish_reason="tool_calls",
                    )

            else:
                # All plan steps are complete — no tool calls needed, route_after_supervisor
                # will see no pending steps and forward to the validator.
                print("[MOCK ADAPTER] Supervisor: all plan steps complete.")
                return GenerateResponse(
                    text="All plan steps are complete. Forwarding to validation.",
                    tool_calls=None,
                    finish_reason="stop",
                )

        # 3. SPECIALIST AGENTS STAGE
        elif "code specialist" in system_prompt or "ROLE: You are the code specialist" in system_prompt:
            if "calculation script" in last_user_content and not self.step_1_failed_once:
                print("[MOCK ADAPTER] CodeAgent simulating first-time step_1 exception.")
                self.step_1_failed_once = True
                return GenerateResponse(
                    text="CRITICAL_FAILURE: Command failed with syntax error.",
                    tool_calls=None,
                    finish_reason="stop",
                )
            else:
                print("[MOCK ADAPTER] CodeAgent simulating successful script execution.")
                return GenerateResponse(
                    text="TASK_COMPLETED: Calculations successfully written to output.txt.",
                    tool_calls=None,
                    finish_reason="stop",
                )

        # 4. VALIDATOR STAGE
        elif "You are Chad's Reflection & Quality Validator" in system_prompt:
            has_fix = any(
                "step_validation_fix" in str(m.get("content", ""))
                for m in messages
            )
            if has_fix:
                print("[MOCK ADAPTER] Validator passing validation.")
                return GenerateResponse(
                    text="VALIDATION_PASSED: Calculations are verified and correct.",
                    tool_calls=None,
                    finish_reason="stop",
                )
            else:
                print("[MOCK ADAPTER] Validator failing validation on first attempt.")
                return GenerateResponse(
                    text="VALIDATION_FAILED: Calculations match, but the output file name is wrong.",
                    tool_calls=None,
                    finish_reason="stop",
                )

        # Fallback
        print("[MOCK ADAPTER] FALLBACK response")
        return GenerateResponse(
            text="Hello from Fallback! TASK_COMPLETED",
            tool_calls=None,
            finish_reason="stop",
        )

    def convert_tool_schemas(self, schemas): return []
    def build_tool_result_message(self, id, name, res): return {}
    def build_assistant_tool_call_message(self, calls, content=None): return {}
    async def is_ready(self): return True


def warmup_mock_registry(exclude: Optional[str] = None):
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    registry = PromptRegistry.get_instance()
    prompts = {
        "planner": "You are Chad's Planner",
        "validator": "You are Chad's Reflection & Quality Validator",
        "supervisor_routing": "ORCHESTRATION & ROUTING",
        "supervisor_skill": "Skill details",
        "code_agent": "ROLE: You are the code specialist",
        "browser_agent": "ROLE: You are the browser specialist",
        "research_agent": "ROLE: You are the research specialist",
        "system_agent": "ROLE: You are the system specialist",
        "ui_automation_agent": "ROLE: You are the UI automation specialist",
        "subagent_base": "OUTPUT PROTOCOL: Return only the result."
    }
    scopes = {
        "planner": "planner",
        "validator": "validator",
        "supervisor_routing": "supervisor",
        "supervisor_skill": "supervisor",
        "code_agent": "code_agent",
        "browser_agent": "browser_agent",
        "research_agent": "research_agent",
        "system_agent": "system_agent",
        "ui_automation_agent": "ui_automation_agent",
        "subagent_base": "subagent_base"
    }
    if exclude:
        prompts.pop(exclude, None)
        scopes.pop(exclude, None)
    registry._prompts = prompts
    registry._scopes = scopes
    registry._loaded = True


def _make_initial_state(turn_id: str = "test_plan_flow") -> ChadState:
    return {
        "messages": [HumanMessage(content="Write a script to calculate Fibonacci sequence numbers.")],
        "turn_id": turn_id,
        "session_id": turn_id,
        "category": "planning",
        "platform": "pc",
        "is_simple": False,
        "active_plan": None,
        "plan_step": "",
        "failure_context": None,
        "validation_feedback": None,
        "system_instruction": "You are Chad. ORCHESTRATION & ROUTING",
        "tools": [],
        "total_tool_calls": 0,
        "tool_results": [],
        "max_tool_calls": 5,
        "final_response": None,
        "temperature": 0.7,
        "thinking_level": "medium",
        "on_token": None,
        "on_thinking": None,
        "variables": {},
    }


@pytest.mark.asyncio
async def test_plan_execute_flow(monkeypatch):
    # monkeypatch (not a raw os.environ assignment) so CHAD_TESTING doesn't
    # leak "true" into later tests in the session.
    monkeypatch.setenv("CHAD_TESTING", "true")
    from src.shared.config_manager import ConfigManager
    ConfigManager.get_config()["disabled_subagents"] = []
    # This test's expected flow (see comment before INVOCATION 2) exercises the
    # post-execution validator node (step_validation_fix), which is gated behind
    # planner.validation_enabled — disabled by default in config/config.yaml since
    # the plan lane's validation pass was flagged as young/costly. Force it on here
    # so this test still covers that path regardless of the production default.
    planner_cfg = ConfigManager.get_config().setdefault("planner", {})
    planner_cfg["validation_enabled"] = True
    # planner.enabled defaults False (plan lane gated off post-open-source launch,
    # issue #402) — this test exercises the plan lane directly, so force it on.
    planner_cfg["enabled"] = True
    warmup_mock_registry()

    adapter = PlanExecuteMockAdapter()
    tool_registry = MockToolRegistry()
    engine = LangGraphEngine(adapter=adapter, tool_executor=tool_registry)

    # 1. Start turn with initial user query
    print("\n--- INVOCATION 1: GENERATE PLAN ---")
    state = await engine.invoke(_make_initial_state(), thread_id="test_plan_flow")

    assert state.get("active_plan") is not None, "Plan should be generated"
    assert len(state["active_plan"]) == 1
    assert state["active_plan"][0]["id"] == "step_1"
    assert state["active_plan"][0]["status"] == "pending"
    assert state["plan_step"] == "step_1"
    assert state.get("graph_interrupt_pending"), "Initial plan should pause for user approval"

    # 2. User approves plan — autonomous replans continue in the same turn (no extra approval pauses)
    print("\n--- INVOCATION 2: APPROVE PLAN AND EXECUTE ---")
    state = await engine.invoke(
        {"messages": [HumanMessage(content="The plan looks perfect. Proceed.")]},
        thread_id="test_plan_flow",
    )

    # Expected flow (single resume), after stall-recovery + planner-directed Scout:
    # • Supervisor delegates step_1 → code_agent CRITICAL_FAILURE
    # • Supervisor calls fail_step → same-step recovery via supervisor (no scout/replan)
    # • Supervisor completes step_1 → validator fails → planner propose_plan step_validation_fix
    # • Supervisor completes step_validation_fix → validator passes → graph ends
    print("\n--- CHECKING FINAL STATE AFTER FULL EXECUTION ---")
    assert state.get("active_plan") is not None, "Plan should still exist"
    assert state["active_plan"][0]["id"] == "step_validation_fix"
    assert state.get("validation_feedback") is None, "Validation feedback should be cleared on pass"
    assert "calculations are verified" in state.get("final_response", "").lower(), (
        f"Expected validation-passed text in final_response, got: {state.get('final_response')!r}"
    )
    print("test_plan_execute_flow passed successfully!")


from unittest.mock import patch

@pytest.mark.asyncio
async def test_plan_execute_missing_prompts(monkeypatch):
    monkeypatch.setenv("CHAD_TESTING", "true")
    from src.shared.config_manager import ConfigManager
    ConfigManager.get_config()["disabled_subagents"] = []
    # planner.enabled defaults to False (#219/#402 public-release feature gate) —
    # without this, route_entry() falls back to the supervisor lane for a
    # "planning" category turn instead of scout->planner, so the planner
    # prompt this test expects to be missing is never even looked up (#216).
    ConfigManager.get_config()["planner"] = {"enabled": True}
    warmup_mock_registry(exclude="planner")

    adapter = PlanExecuteMockAdapter()
    tool_registry = MockToolRegistry()
    engine = LangGraphEngine(adapter=adapter, tool_executor=tool_registry)

    with pytest.raises(Exception) as exc_info:
        await engine.invoke(_make_initial_state("test_missing_prompts"), thread_id="test_missing_prompts")
    # Prompts resolve from local files now (#589), so the error points at the missing
    # fragment file rather than at Supabase.
    assert "Persona fragment 'planner' is missing or empty" in str(exc_info.value)


if __name__ == "__main__":
    if os.name == "nt":
        import sys
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_plan_execute_flow())
    asyncio.run(test_plan_execute_missing_prompts())
