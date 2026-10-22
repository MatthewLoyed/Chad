"""Chad Code headless runner — executed INSIDE the OpenHands container.

Runs the OpenHands agent SDK against the mounted /workspace using a local model,
with no app-server and no sandbox spawning (which avoids the Windows `exposed_urls`
startup race). Chad's `dispatch_coding_task` tool `docker run`s the OpenHands image
with this script mounted at /chad and this file as the entrypoint.

Config is passed via env (see dispatch tool):
  CHAD_TASK           the task brief (required)
  CHAD_MODEL          LiteLLM model id, e.g. openai/qwen/qwen3.6-35b-a3b
  CHAD_LLM_BASE_URL   default http://host.docker.internal:1234/v1  (from the container)
  CHAD_LLM_API_KEY    default lmstudio
  CHAD_MAX_ITER       default 30
  CHAD_TEMPERATURE    default 0    (deterministic first pass — #582 research)
  CHAD_NUM_RETRIES    default 2    (cap LiteLLM's default 8 so a stuck model fails fast)
  CHAD_LLM_TIMEOUT_S default 900  (per request; long local file generations can exceed 300s)

Capability upgrades (all optional, all graceful — a failure to wire any of these falls
back to the proven base agent so a dispatch never breaks because a capability is off):
  CHAD_SKILLS_DIR     dir of OpenHands skills (AgentSkills `skills/<name>/SKILL.md`) to
                      persistently augment the system prompt. Default /chad/skills.
  CHAD_MCP_CONFIG     JSON `{"mcpServers": {...}}` — HTTP/SSE MCP servers (e.g. GitHub).
                      stdio servers need npx/uvx which the base image lacks, so prefer
                      hosted HTTP MCPs. Empty/absent = no MCP.
  CHAD_STREAM_EVENTS  "1" (default) to stream a compact `CHAD_EVENT:` line per agent
                      event to stdout so the job is watchable live via `docker logs`
                      (visibility — issue #575), not just via a poll-time snapshot.

Tool mode is model-specific. The legacy Qwen baseline uses text parsing; LFM2.5
uses native tool_calls returned by LM Studio. Dispatch resolves the per-model
setting before passing CHAD_NATIVE_TOOL_CALLING to this runner.
"""

import json
import os
import sys
import threading

from chad_run_validation import IncompleteRunError, validate_conversation

import openhands.tools  # noqa: F401 — registers the `terminal` + `file_editor` tools
from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    LocalWorkspace,
    Message,
    TextContent,
    Tool,
)

_SENTINEL_DONE = "CHAD_DISPATCH_DONE"
_SENTINEL_ERR = "CHAD_DISPATCH_ERROR"


def _load_skills(skills_dir: str):
    """Load OpenHands skills from a mounted dir (AgentSkills `skills/<name>/SKILL.md`).
    Returns a flat list of Skill objects, or [] on any problem. Never raises — a broken
    skill must not sink the whole dispatch."""
    if not skills_dir or not os.path.isdir(skills_dir):
        return []
    try:
        from openhands.sdk import load_skills_from_dir

        repo, knowledge, agent_sk = load_skills_from_dir(skills_dir)
        skills = list({**repo, **knowledge, **agent_sk}.values())
        if skills:
            print(f"CHAD_SKILLS loaded: {[getattr(s, 'name', '?') for s in skills]}")
        return skills
    except Exception as exc:  # noqa: BLE001 — capability is best-effort
        print(f"CHAD_SKILLS load failed (non-fatal): {type(exc).__name__}: {exc}")
        return []


def _parse_mcp_config(raw: str):
    """Parse CHAD_MCP_CONFIG JSON into an mcp_config dict, or None if absent/invalid."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
        servers = (cfg or {}).get("mcpServers") or {}
        if not servers:
            return None
        print(f"CHAD_MCP servers: {list(servers.keys())}")
        return cfg
    except Exception as exc:  # noqa: BLE001
        print(f"CHAD_MCP config parse failed (non-fatal): {type(exc).__name__}: {exc}")
        return None


def _make_event_streamer():
    """A Conversation callback that prints a compact, secret-safe line per agent event so a
    running job is watchable live via `docker logs` (issue #575). Best-effort — any error in
    the callback is swallowed so it can never crash the run."""
    def _cb(event) -> None:
        try:
            # Surface the model's reasoning on its own CHAD_THOUGHT: line (up to 600 chars, vs the
            # 200 for actions) — when a run stalls or loops, the thought is the single most useful
            # signal for diagnosing WHY (issue #582: evals must capture model thoughts, not just
            # tool calls). The probe greps these for its post-run forensics.
            thought = getattr(event, "thought", None) or getattr(event, "reasoning_content", None)
            if thought:
                t = " ".join(str(thought).split())
                print(f"CHAD_THOUGHT: {t[:597] + '...' if len(t) > 600 else t}")
            kind = type(event).__name__
            summary = ""
            for attr in ("action", "tool_name", "message", "content", "observation"):
                val = getattr(event, attr, None)
                if val:
                    summary = str(val)
                    break
            summary = " ".join(summary.split())  # collapse whitespace
            if summary:
                if len(summary) > 200:
                    summary = summary[:197] + "..."
                print(f"CHAD_EVENT: {kind} {summary}".rstrip())
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 — telemetry must never break the run
            pass
    return _cb


def _arm_watchdog(timeout_s: int) -> None:
    """Wall-clock runaway protection: a stuck agent/model loop is bounded by max_iter, but a
    single call can still hang indefinitely. Force-exit the container past a hard deadline so a
    job can never hang forever even if Chad never polls it (issue #576)."""
    if timeout_s <= 0:
        return

    def _kill() -> None:
        print(f"{_SENTINEL_ERR}: wall-clock timeout after {timeout_s}s — killing job")
        sys.stdout.flush()
        os._exit(1)

    t = threading.Timer(timeout_s, _kill)
    t.daemon = True
    t.start()


def main() -> int:
    task = (os.environ.get("CHAD_TASK") or "").strip()
    if not task:
        print(f"{_SENTINEL_ERR}: no CHAD_TASK provided")
        return 2

    model = os.environ.get("CHAD_MODEL", "openai/qwen/qwen3.6-35b-a3b")
    base_url = os.environ.get("CHAD_LLM_BASE_URL", "http://host.docker.internal:1234/v1")
    api_key = os.environ.get("CHAD_LLM_API_KEY", "lmstudio")
    try:
        max_iter = int(os.environ.get("CHAD_MAX_ITER", "30"))
    except ValueError:
        max_iter = 30

    # Dispatch passes the resolved per-model mode. False preserves the legacy
    # Qwen text parser; LFM's LM Studio endpoint uses structured native calls.
    native_tc = (os.environ.get("CHAD_NATIVE_TOOL_CALLING", "0").strip().lower()
                 in ("1", "true", "yes", "on"))
    # task_tracker is OpenHands' native plan/checklist tool — the agent maintains a TODO list
    # and checks items off. OFF by default (A/B showed a latency/token tax with no gain at
    # typical task sizes); worth enabling for genuinely large multi-step tasks (issue #574).
    use_tracker = (os.environ.get("CHAD_USE_TASK_TRACKER", "0").strip().lower()
                   in ("1", "true", "yes", "on"))
    # Sub-agent delegation (issue #358). Each sub-agent gets its OWN conversation context and
    # returns only a result, so a long exploration doesn't pollute the parent's history — the
    # same lever Chad's own subagents pull. NOTE: this is TaskToolSet, not DelegateTool;
    # DelegateTool is deprecated as of SDK 1.16.0 and removed in 1.23.0 (image ships 1.17.0).
    # OFF by default until A/B'd — a 3B-active MoE orchestrating sub-agents is unproven
    # (MCPMark 37.0 suggests moderate, not strong, tool-orchestration ability).
    use_subagents = (os.environ.get("CHAD_USE_SUBAGENTS", "0").strip().lower()
                     in ("1", "true", "yes", "on"))
    # Context condenser: summarizes old events once history exceeds max_size, so a long job
    # can't die of context overflow (~2x cost reduction upstream). OFF by default until A/B'd:
    # condensing too eagerly can quietly make the coder dumber on multi-file work by summarizing
    # away a file it still needs. keep_first pins the opening events (the brief) so the task
    # statement itself is never summarized away.
    use_condenser = (os.environ.get("CHAD_USE_CONDENSER", "0").strip().lower()
                     in ("1", "true", "yes", "on"))
    try:
        condenser_max_size = int(os.environ.get("CHAD_CONDENSER_MAX_SIZE", "240"))
    except ValueError:
        condenser_max_size = 240
    try:
        condenser_keep_first = int(os.environ.get("CHAD_CONDENSER_KEEP_FIRST", "2"))
    except ValueError:
        condenser_keep_first = 2
    # Production critic (EmptyPatchCritic, issue #584): catches a run that finishes claiming
    # success but left no git diff — a real, cheap failure mode for small local coders. Distinct
    # from the eval critic below (that one is DEV-ONLY, self-eval). OFF by default pending an A/B.
    use_critic = (os.environ.get("CHAD_USE_CRITIC", "0").strip().lower()
                  in ("1", "true", "yes", "on"))
    # Eval critic (issue #358): a real Chad eval suite becomes the run's success signal, run on
    # the HOST via the bridge (the coder and the main model can't share 16GB of VRAM). Empty
    # means no critic — the ordinary "coder builds a thing" path is unaffected.
    eval_suite = (os.environ.get("CHAD_EVAL_SUITE", "") or "").strip()
    eval_case = (os.environ.get("CHAD_EVAL_CASE", "") or "").strip() or None
    eval_bridge = (os.environ.get("CHAD_EVAL_BRIDGE_URL", "http://host.docker.internal:8799")).strip()
    try:
        eval_max_attempts = int(os.environ.get("CHAD_EVAL_MAX_ATTEMPTS", "3"))
    except ValueError:
        eval_max_attempts = 3

    try:
        timeout_s = int(os.environ.get("CHAD_TIMEOUT_S", "1800"))
    except ValueError:
        timeout_s = 1800
    _arm_watchdog(timeout_s)

    # Model tuning (issue #582 research → adopt). Deterministic first pass: temperature 0 is the
    # well-evidenced default for coding-agent tasks (a small local coder wanders less at t=0). The
    # retry cap fights a different failure: LiteLLM defaults to 8 retries per call, so a genuinely
    # stuck/erroring local model can burn ~8x the wall-clock on every step before the watchdog or
    # max_iter ever fires — capping it low makes a bad job fail fast instead of grinding. Both are
    # env-surfaced and applied with a graceful fallback below (the SDK LLM model forbids unknown
    # fields, so a field-name mismatch on some image version must not sink the dispatch).
    try:
        temperature = float(os.environ.get("CHAD_TEMPERATURE", "0"))
    except ValueError:
        temperature = 0.0
    try:
        num_retries = int(os.environ.get("CHAD_NUM_RETRIES", "2"))
    except ValueError:
        num_retries = 2
    try:
        llm_timeout_s = max(1, int(os.environ.get("CHAD_LLM_TIMEOUT_S", "900")))
    except ValueError:
        llm_timeout_s = 900

    # --- Optional capability upgrades (all graceful; failures fall back to base) ---
    skills_dir = os.environ.get("CHAD_SKILLS_DIR", "/chad/skills")
    skills = _load_skills(skills_dir)
    # Self-eval-only skills (issue #358): how to read Chad's own eval output and where Chad's
    # architecture lives. Loaded ONLY when the eval critic is active (eval_suite set), so an
    # ordinary user coding job never sees them. DEV-ONLY — see evals/SELF_EVAL_DO_NOT_SHIP.md.
    if eval_suite:
        skills += _load_skills(os.environ.get("CHAD_SELFEVAL_SKILLS_DIR", "/chad/skills_selfeval"))
    mcp_config = _parse_mcp_config(os.environ.get("CHAD_MCP_CONFIG", ""))
    stream_events = (os.environ.get("CHAD_STREAM_EVENTS", "1").strip().lower()
                     in ("1", "true", "yes", "on"))

    critic_mode = "eval" if eval_suite else ("empty_patch" if use_critic else "off")
    print(f"CHAD_DISPATCH_START model={model} max_iter={max_iter} "
          f"native_tool_calling={native_tc} task_tracker={use_tracker} timeout_s={timeout_s} "
          f"temperature={temperature} num_retries={num_retries} "
          f"subagents={use_subagents} condenser={use_condenser} critic={critic_mode} "
          f"skills={len(skills)} mcp={'yes' if mcp_config else 'no'} stream={stream_events}")
    print(f"CHAD_TASK: {task}")
    sys.stdout.flush()

    _base_llm_kwargs = dict(
        model=model,
        base_url=base_url,
        api_key=api_key,
        usage_id="agent",
        native_tool_calling=native_tc,
        timeout=llm_timeout_s,
    )
    try:
        llm = LLM(**_base_llm_kwargs, temperature=temperature, num_retries=num_retries)
    except Exception as exc:  # noqa: BLE001 — unknown field on this image version must not sink the job
        print(f"CHAD_LLM tuning fields rejected, falling back to base LLM "
              f"(non-fatal): {type(exc).__name__}: {exc}")
        llm = LLM(**_base_llm_kwargs)
    tools = [Tool(name="terminal"), Tool(name="file_editor")]
    if use_tracker:
        tools.append(Tool(name="task_tracker"))
    if use_subagents:
        # Guarded: TaskToolSet only exists on newer SDKs. A missing toolset must degrade to
        # "no sub-agents", never sink the dispatch.
        try:
            from openhands.tools.task import TaskToolSet

            tools.append(Tool(name=TaskToolSet.name))
            print(f"CHAD_SUBAGENTS enabled (TaskToolSet name={TaskToolSet.name})")
        except Exception as exc:  # noqa: BLE001
            print(f"CHAD_SUBAGENTS unavailable on this image, continuing without "
                  f"(non-fatal): {type(exc).__name__}: {exc}")

    # Condenser is built once and shared by both build attempts (MCP and no-MCP fallback).
    # It needs its own LLM handle so condensation tokens are accounted separately from the
    # agent's. Any failure here degrades to no condenser rather than failing the job.
    condenser = None
    if use_condenser:
        try:
            from openhands.sdk.context.condenser import LLMSummarizingCondenser

            condenser_llm = LLM(**_base_llm_kwargs | {"usage_id": "condenser"})
            condenser = LLMSummarizingCondenser(
                llm=condenser_llm,
                max_size=condenser_max_size,
                keep_first=condenser_keep_first,
            )
            print(f"CHAD_CONDENSER enabled max_size={condenser_max_size} "
                  f"keep_first={condenser_keep_first}")
        except Exception as exc:  # noqa: BLE001
            print(f"CHAD_CONDENSER wiring failed, continuing without "
                  f"(non-fatal): {type(exc).__name__}: {exc}")
            condenser = None

    # codegraph's `codegraph_search` tool declares a `kind` parameter, which collides with the
    # `kind` discriminator (a computed_field) on OpenHands' MCP-action base class and hard-crashes
    # the run at tool-registration (verified live #582). Exclude just that one tool via
    # filter_tools_regex (keep = name matches) — the PRIMARY codegraph_explore plus callers/callees/
    # impact/node/files/status have no `kind` and stay available. `.*codegraph_search` guards
    # against OpenHands namespacing the MCP tool name with its server key.
    codegraph_tool_filter = r"^(?!.*codegraph_search).*$"

    # Critic: structural verification. Unlike a tool, the agent cannot decline to run it, and
    # IterativeRefinementConfig turns #358's "max 3 attempts per failure" into a declarative
    # budget — Conversation.run() re-prompts with the failure and retries on its own.
    critic = None
    if eval_suite:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from chad_eval_critic import ChadEvalCritic  # noqa: E402
            from openhands.sdk.critic import IterativeRefinementConfig

            critic = ChadEvalCritic(
                bridge_url=eval_bridge,
                suite=eval_suite,
                case_id=eval_case,
                # HOST path of what's mounted at /workspace — the container can't infer it.
                workspace=os.environ.get("CHAD_EVAL_WORKSPACE", "") or None,
                timeout_s=timeout_s,
                # An eval is a hard gate: score is 1.0 or 0.0, so any threshold above 0 means
                # "must pass". 0.99 rather than 1.0 to avoid float-equality edge cases.
                iterative_refinement=IterativeRefinementConfig(
                    success_threshold=0.99,
                    max_iterations=eval_max_attempts,
                ),
            )
            print(f"CHAD_EVAL_CRITIC enabled suite={eval_suite} case={eval_case} "
                  f"max_attempts={eval_max_attempts} bridge={eval_bridge}")
        except Exception as exc:  # noqa: BLE001
            # No critic is a worse run, not a dead run — same contract as the MCP fallback.
            print(f"CHAD_EVAL_CRITIC wiring failed, continuing WITHOUT verification "
                  f"(non-fatal): {type(exc).__name__}: {exc}")
            critic = None
    elif use_critic:
        try:
            from openhands.sdk.critic import EmptyPatchCritic, IterativeRefinementConfig

            critic = EmptyPatchCritic(
                # A no-op patch scores 0.0 — any threshold above 0 forces a retry; 0.99 rather
                # than 1.0 avoids float-equality edge cases (same convention as the eval critic).
                iterative_refinement=IterativeRefinementConfig(
                    success_threshold=0.99,
                    max_iterations=2,
                ),
            )
            print("CHAD_CRITIC enabled mode=empty_patch max_iterations=2")
        except Exception as exc:  # noqa: BLE001
            print(f"CHAD_CRITIC wiring failed, continuing WITHOUT verification "
                  f"(non-fatal): {type(exc).__name__}: {exc}")
            critic = None

    def _build_and_run(use_mcp: bool) -> None:
        """Build the agent + conversation and run the task once. Raises on failure so the caller
        can decide whether to retry without MCP. AgentContext carries the skills (persistent
        system-prompt augmentation); mcp_config adds the MCP toolsets."""
        agent_kwargs = {"llm": llm, "tools": tools}
        if condenser is not None:
            agent_kwargs["condenser"] = condenser
        if critic is not None:
            agent_kwargs["critic"] = critic
        if skills:
            try:
                from openhands.sdk import AgentContext

                agent_kwargs["agent_context"] = AgentContext(skills=skills)
            except Exception as exc:  # noqa: BLE001
                print(f"CHAD_SKILLS attach failed (non-fatal): {type(exc).__name__}: {exc}")
        if use_mcp and mcp_config:
            agent_kwargs["mcp_config"] = mcp_config
            agent_kwargs["filter_tools_regex"] = codegraph_tool_filter
        agent = Agent(**agent_kwargs)

        conv_kwargs = {
            "agent": agent,
            "workspace": LocalWorkspace(working_dir="/workspace"),
            "max_iteration_per_run": max_iter,
        }
        if stream_events:
            conv_kwargs["callbacks"] = [_make_event_streamer()]
        try:
            conv = Conversation(**conv_kwargs)
        except Exception as exc:  # noqa: BLE001 — streaming callback must not block the job
            print(f"CHAD_CONV callback wiring failed, falling back (non-fatal): "
                  f"{type(exc).__name__}: {exc}")
            conv = Conversation(agent=agent, workspace=LocalWorkspace(working_dir="/workspace"),
                                max_iteration_per_run=max_iter)

        try:
            conv.send_message(task)
        except Exception:
            conv.send_message(Message(role="user", content=[TextContent(text=task)]))
        conv.run()
        validate_conversation(conv)

    # Run with MCP if configured. If ANYTHING in the MCP-enabled path fails, degrade to the proven
    # base agent (terminal + file_editor) instead of crashing the whole job — an MCP tool whose
    # schema OpenHands can't build surfaces the failure at conv.run(), *past* agent construction, so
    # a try/except only around Agent() (the old code) let it kill the job. This is the real
    # regression guard: enabling an MCP must never be worse than not having it (#582).
    try:
        _build_and_run(use_mcp=bool(mcp_config))
    except IncompleteRunError:
        # Retrying an incomplete task without MCP could repeat side effects.
        raise
    except Exception as exc:  # noqa: BLE001
        if not mcp_config:
            raise
        print(f"CHAD_MCP run failed, retrying WITHOUT MCP (graceful fallback): "
              f"{type(exc).__name__}: {exc}")
        sys.stdout.flush()
        _build_and_run(use_mcp=False)

    print(_SENTINEL_DONE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — surface any crash to the transcript
        print(f"{_SENTINEL_ERR}: {type(exc).__name__}: {exc}")
        sys.exit(1)
