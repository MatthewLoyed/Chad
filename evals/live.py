r"""
live.py — importable live probe (Phase 1 live lane) + workflow lane (plan→approve→execute).

Distilled from scripts/trace_probe.py into a quiet, importable coroutine so the eval
runner can push a case through the REAL running Brain server and capture the actual
output: final response, executed tool calls, reasoning, and the planner's plan.

This unlocks the judges that need real model output (planner_quality, response_grounded)
and lets assertions check *executed* tools (even stronger than "bound" in the dry-run lane).

Requires a running server (start it from this worktree to run on the local model — free):
    $env:PYTHONPATH="."; $env:CHAD_DAEMONS_ENABLED="false"; .venv\Scripts\python.exe src\remote\server.py
"""

import asyncio
import json
import os
import time
import uuid
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SERVER_WS = os.getenv("CHAD_SERVER_URL", "ws://localhost:8767/ws")


def _health_url_from_ws(ws_url: str) -> str:
    """Derive the http(s) /health URL from the configured WS URL so the eval lanes
    can target a dev Brain (e.g. :8788) instead of the hardcoded prod :8767."""
    from urllib.parse import urlparse
    p = urlparse(ws_url)
    scheme = "https" if p.scheme in ("wss", "https") else "http"
    host = p.hostname or "localhost"
    port = f":{p.port}" if p.port else ""
    return f"{scheme}://{host}{port}/health"


HEALTH_URL = _health_url_from_ws(SERVER_WS)
FIRST_EVENT_TIMEOUT = int(os.getenv("EVAL_FIRST_EVENT_TIMEOUT", "300"))
# Progress-based stall detection (preferred over a blunt wall-clock cap). A healthy turn emits a
# steady stream of events (reasoning chunks, tool_start/tool_end, plan snapshots). We only fail a
# round when it goes SILENT — no server event at all — for IDLE_CHECK_S, MAX_IDLE_CHECKS times in a
# row (i.e. genuinely stalled, not merely slow). This is why an ideal-but-slow deep-research round
# is allowed to run long while a truly hung one is caught in a few minutes, instead of a hard cap
# killing legitimate work (or a too-generous cap hiding a hang). The wall-clock values below are
# just large backstops against an infinite event stream.
ROUND_IDLE_CHECK_S = int(os.getenv("EVAL_ROUND_IDLE_CHECK_S", "180"))      # check for progress every 3 min
MAX_IDLE_CHECKS = int(os.getenv("EVAL_ROUND_MAX_IDLE_CHECKS", "2"))         # 2 silent checks in a row => stalled
RESPONSE_TIMEOUT = int(os.getenv("EVAL_WORKFLOW_ROUND_TIMEOUT_S", "7200"))  # backstop only; idle-detection is primary
# probe_live is a SINGLE turn (not a workflow round). Idle-detection still applies; this is the
# wall-clock backstop.
LIVE_TURN_TIMEOUT = int(os.getenv("EVAL_LIVE_TURN_TIMEOUT_S", "1800"))
# How much of each tool result and the aggregate to retain for the grounding judge.
# 0 = unlimited (HITL quality lane: judge needs raw Drive bodies, not headers).
TOOL_RESULT_CHARS = int(os.getenv("EVAL_TOOL_RESULT_CHARS", "0"))
TOOL_RESULTS_TOTAL_CHARS = int(os.getenv("EVAL_TOOL_RESULTS_TOTAL_CHARS", "0"))
# Priority/platform the eval sends on user_input. `platform="eval"` marks the turn for
# eval exclusivity (conversation_orchestrator.eval_turn_guard): eval turns run at
# interactive model-lifecycle priority and block desktop/cron/other non-eval turns for
# their duration — absolute precedence in development. Previously eval was tagged
# background and waited ~interactive_active_window_s behind any recent interactive touch
# (~30s dead time per live case). Override with CHAD_EVAL_PLATFORM only for experiments.
EVAL_PLATFORM = os.getenv("CHAD_EVAL_PLATFORM", "eval")
# Canned answer the workflow eval feeds an approval-gated interactive tool (ask_user_question)
# so an unattended run can proceed to its deliverable instead of deadlocking. Deliberately tells
# the model to stop asking and finish — a real cooperative user would supply defaults, not babysit.
# Plan-approval follow-up. Avoid a bare "proceed" — Brain used to latch that as plan_goal
# and re-enter scout/planner (HITL 2026-07-24). Keep the wording as an approval of the
# *existing* plan, not a new user ask.
WORKFLOW_APPROVAL_ANSWER = os.getenv(
    "EVAL_WORKFLOW_APPROVAL_ANSWER",
    "Approved — execute the existing plan now. Do not create a new plan. "
    "Call the tools/delegates for each step and call complete_step as you finish. "
    "Use your best judgment with reasonable defaults; do not pause to ask further "
    "questions unless you are truly blocked.",
)
WORKFLOW_QUESTION_ANSWER = os.getenv(
    "EVAL_WORKFLOW_QUESTION_ANSWER",
    "Use your best judgment with reasonable defaults for any missing details: "
    "choose an educational/relevant video, visualize the available data for the chart, "
    "search memory/contacts for any needed information, and proceed with creating the plan.",
)
# When the supervisor ends an execution turn with the plan UNFINISHED and no pause (the
# "I'll continue now" status-update-and-yield failure), a real user would just say "keep going".
# The harness does exactly that, but bounded — and counts the nudges, because a run that needs a
# nudge every round is failing to drive itself autonomously (the anchoring weakness we measure).
MAX_CONTINUE_NUDGES = int(os.getenv("EVAL_WORKFLOW_MAX_CONTINUE_NUDGES", "3"))
CONTINUE_NUDGE = os.getenv(
    "EVAL_WORKFLOW_CONTINUE_NUDGE",
    "Continue executing the existing plan unless it is completely done, and if it is done, "
    "then emit `complete_step`. Do not create a new plan. Do not just describe or narrate what "
    "you did; emit the necessary tool calls to advance, or call `complete_step` if the work is finished.",
)
# Agent-lane turns have NO plan, so the plan-progress check above can never fire for them —
# a code_agent that read the target file and then ended with "Task completed." was never
# pushed to actually produce a diff (#566). `completion_check` closes that: a caller supplies
# a predicate over OBSERVABLE STATE (for coder_bench, "is `git diff` non-empty in the
# worktree"), and a turn that ends with the predicate unsatisfied gets nudged.
#
# Anchoring the stop condition to observable state rather than the model's own claim is the
# remedy the literature converges on for this failure mode — "premature completion" /
# "stopping-criterion misalignment", where an agent fires its done-token on the first sign of
# progress. Generic "be thorough" instructions measurably do NOT help; a specific, observable
# gap does. See docs/evals/PREMATURE_COMPLETION.md for sources.
COMPLETION_NUDGE = os.getenv(
    "EVAL_WORKFLOW_COMPLETION_NUDGE",
    "You ended your turn but the task is not done: {gap}. Do not describe what you would do "
    "and do not ask whether to proceed — make the change now with the editing tools, then stop.",
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _maybe_truncate(text: str, limit: int) -> str:
    """If limit <= 0, keep the full string (quality/HITL default)."""
    if not text or limit is None or limit <= 0 or len(text) <= limit:
        return text
    return text[:limit]


def _record_thinking(
    trace: "LiveTrace",
    reasoning: List[str],
    *,
    agent: str,
    text: str,
    state: str = "reasoning",
    append: bool = True,
) -> None:
    """Append a reasoning/thinking delta, coalescing consecutive same-agent chunks.

    The Brain WS streams one ``ai_thought`` frame per token/delta with ``append=True``
    (see ``UIStateSyncer._broadcast_thinking_update``). The GUI coalesces those into one
    block; the eval harness used to record each delta as its own event, which blew
    transcripts to thousands of one-word ``### Thinking`` sections (HITL golfport brief:
    ~6874 thinking headers). Merge consecutive same-agent deltas here.
    """
    if not text:
        return
    agent = agent or "Main"
    reasoning.append(text)

    last_ev = trace.events[-1] if trace.events else None
    can_merge = (
        append
        and last_ev is not None
        and last_ev.get("type") == "thinking"
        and (last_ev.get("agent") or "Main") == agent
    )
    if can_merge:
        last_ev["text"] = (last_ev.get("text") or "") + text
        if (
            trace.reasoning_chunks
            and (trace.reasoning_chunks[-1].get("agent") or "Main") == agent
        ):
            trace.reasoning_chunks[-1]["text"] = (
                (trace.reasoning_chunks[-1].get("text") or "") + text
            )
        return

    trace.reasoning_chunks.append({
        "agent": agent,
        "state": state,
        "text": text,
        "seq": len(trace.reasoning_chunks),
    })
    # Only stamp timeline once per coalesced block (not per token).
    trace.timeline.append(f"reasoning:{agent}")
    trace.events.append({"type": "thinking", "agent": agent, "text": text})


@dataclass
class LiveTrace:
    prompt: str
    category: str
    status: str = "failure"             # "success" | "failure"
    final_response: Optional[str] = None
    reasoning_full: str = ""
    reasoning_chunks: List[Dict[str, Any]] = field(default_factory=list)
    tool_names: List[str] = field(default_factory=list)     # executed tools (across all rounds)
    # Full {name, params} per call, in order — tool_names alone can't tell you WHAT ref/target/
    # value a browser_click or browser_type call actually used, which is exactly what you need to
    # tell "harness fed the model a stale ref" apart from "model picked the wrong ref" (see the
    # browser-agent hallucination-under-failed-navigation findings that motivated this field).
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: str = ""
    plan: Optional[List[Dict[str, Any]]] = None
    duration_s: float = 0.0
    total_tokens: int = 0                # approx (reasoning + tool_results + final_response), 4 chars/token
    error: Optional[str] = None
    # ── Workflow lane (plan → approve → execute) ──────────────────────────────
    stop_reason: Optional[str] = None   # last turn's stop_reason (awaiting_approval/_input/task_completed/…)
    rounds: int = 1                      # number of turns the probe drove (1 = single-turn)
    completed: bool = False              # workflow reached a real completion (not stuck paused)
    timeline: List[str] = field(default_factory=list)   # ordered: "tool:<name>", "plan"
    # Chronological event log for transcripts/judges: thinking → tool_start → tool_end → …
    # Each item is {"type": "thinking"|"tool_start"|"tool_end"|"plan", ...}. Prefer this over
    # the isolated reasoning / tools / results sections when rendering.
    events: List[Dict[str, Any]] = field(default_factory=list)
    gather_brief: Optional[str] = None
    # The plan the ExecutionPlanEvent carries EVOLVES across rounds (step statuses flip
    # pending → running → completed). `plan` above holds the FIRST proposal (for min_steps and
    # "what was intended"); `final_plan` holds the LAST snapshot (actual progress). Keeping only
    # the first — as the probe used to — made every stalled run look identical: you could never
    # see "planned 8 steps, completed 2, stuck on step 3", which is THE diagnostic for a plan-lane
    # run that lost the thread. See render_transcript / the stuck-error message.
    final_plan: Optional[List[Dict[str, Any]]] = None
    goal: Optional[str] = None           # original objective (ExecutionPlanEvent.goal) — the "north star"
    rounds_detail: List[Dict[str, Any]] = field(default_factory=list)  # per-round progress summary
    # Approval-gated interactive tool (e.g. ask_user_question): the server pauses on an
    # approval token and will not advance until the client sends a tool_approval_response.
    # A workflow eval has no human, so probe_workflow auto-answers the token, then drives
    # the normal "proceed" follow-up. Without this, an approval-gated ask_user_question mid-run
    # deadlocks the whole workflow (the run is not stalled — it is correctly waiting for input).
    pending_approval_token: Optional[str] = None
    pending_approval_tool: Optional[str] = None
    # Full human-facing content of a pending approval (populated whenever pending_approval_token
    # is set) — the prompt text, title, and (for ask_user_question) the structured questions/options
    # list. probe_workflow's default (non-interactive) auto-answer never needed these; the
    # interactive lane (`interactive=True`) surfaces them so a real caller (human or an agent
    # driving the eval) can read the actual question instead of blindly feeding a canned answer.
    pending_approval_prompt: Optional[str] = None
    pending_approval_title: Optional[str] = None
    pending_approval_questions: Optional[List[Dict[str, Any]]] = None
    # Set when interactive=True and the run stopped to hand control back to the caller rather than
    # auto-answering. False on a normal completion/plan-approval-only pause.
    awaiting_human: bool = False
    continue_nudges: int = 0             # times the harness had to say "keep going" (autonomy deficit)
    # Set when a caller-supplied `completion_check` was still unsatisfied at the end of the
    # run — the observable gap, in the predicate's own words ("no file was modified"). None
    # means either satisfied or no predicate supplied.
    completion_gap: Optional[str] = None
    session_id: Optional[str] = None     # conv id used — key to fetch the full LangSmith trace tree

    @property
    def steps_completed(self) -> int:
        return _plan_progress(self.final_plan or self.plan)[0]

    @property
    def steps_total(self) -> int:
        return _plan_progress(self.final_plan or self.plan)[1]

    @property
    def tool_count(self) -> int:
        return len(self.tool_names)

    @property
    def reasoning_by_agent(self) -> Dict[str, str]:
        buckets: Dict[str, List[str]] = {}
        for chunk in self.reasoning_chunks:
            agent = chunk.get("agent") or "Main"
            buckets.setdefault(agent, []).append(chunk.get("text") or "")
        return {agent: "".join(parts) for agent, parts in buckets.items()}


# ── Auth token (env → .env, walking up from the worktree) ────────────────────


def load_auth_token() -> Optional[str]:
    for key in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    candidate = PROJECT_ROOT
    for _ in range(6):
        dotenv = candidate / ".env"
        if dotenv.exists():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN"):
                    val = v.strip().strip('"').strip("'")
                    if val:
                        return val
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def server_is_up() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5.0) as r:
            return r.status == 200
    except Exception:
        return False


def _msg(msg_type: str, payload: dict = None, auth_token: str = None) -> str:
    return json.dumps({
        "id": str(uuid.uuid4())[:8], "type": msg_type,
        "payload": payload or {}, "auth_token": auth_token, "timestamp": time.time(),
    })


# ── Shared WS session (one connect+auth per live/workflow suite) ─────────────


@asynccontextmanager
async def open_eval_ws(auth_token: Optional[str] = None, *, ui_mode: str = "agent"):
    """Connect + auth once; reuse across probe_live / probe_workflow cases (~2s saved/case).

    Auth identifies as ``client_type=eval`` with ``ui_mode`` defaulting to ``agent`` so
    connect-preload warms the tool selector — never GUI ``auto`` (intent-router gemma-1b).
    Pass ``ui_mode="plan"`` for planner-heavy suites.
    """
    import websockets

    token = auth_token or load_auth_token()
    if not token:
        raise RuntimeError("no auth token (set CHAD_REMOTE_TOKEN or add it to .env)")

    headers = {"Origin": "http://localhost:5173"}
    try:
        ws_ctx = websockets.connect(SERVER_WS, additional_headers=headers,
                                    ping_interval=None, open_timeout=30)
    except TypeError:
        ws_ctx = websockets.connect(SERVER_WS, extra_headers=headers,
                                    ping_interval=None, open_timeout=30)

    async with ws_ctx as ws:
        mode = (ui_mode or "agent").strip().lower()
        if mode in ("planning", "planner"):
            mode = "plan"
        if mode == "auto":
            mode = "agent"
        await ws.send(_msg(
            "auth",
            payload={"client_type": "eval", "ui_mode": mode},
            auth_token=token,
        ))
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=35.0))
        if reply.get("type") != "auth" or reply.get("payload", {}).get("status") != "success":
            raise RuntimeError(f"auth rejected: {reply.get('payload')}")
        yield ws



async def drain_ws(ws, timeout: float = 0.05) -> int:
    """Drain any leftover unread messages from a reused WebSocket connection."""
    drained = 0
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
            drained += 1
        except (asyncio.TimeoutError, Exception):
            break
    return drained


# ── One live turn over a fresh WS connection ─────────────────────────────────


async def probe_live(prompt: str, *, category: str = "agent",
                     conv_id: Optional[str] = None,
                     auth_token: Optional[str] = None,
                     ws=None) -> LiveTrace:
    """Drive one live turn. Pass ``ws`` from ``open_eval_ws`` to skip reconnect+auth."""
    trace = LiveTrace(prompt=prompt, category=category)
    token = auth_token or load_auth_token()
    if not token:
        trace.error = "no auth token (set CHAD_REMOTE_TOKEN or add it to .env)"
        return trace

    conv = conv_id or f"eval_live_{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    try:
        if ws is not None:
            await _probe_live_on_ws(ws, trace, prompt, category, conv, token)
        else:
            async with open_eval_ws(token) as owned:
                await _probe_live_on_ws(owned, trace, prompt, category, conv, token)
    except Exception as e:
        trace.error = f"{type(e).__name__}: {e}"

    trace.duration_s = round(time.time() - t0, 1)
    trace.total_tokens = _estimate_tokens(trace)
    return trace


async def _probe_live_on_ws(ws, trace: LiveTrace, prompt: str, category: str,
                            conv: str, token: str) -> None:
    await drain_ws(ws)
    # platform="eval" — exclusive interactive priority; see EVAL_PLATFORM comment.
    await ws.send(_msg("user_input", {
        "text": prompt, "initiator": "user",
        "conversation_id": conv, "category": category, "platform": EVAL_PLATFORM,
    }, auth_token=token))

    reasoning: List[str] = []
    results: List[str] = []
    try:
        await asyncio.wait_for(_collect(ws, trace, reasoning, results, expected_conv=conv),
                               timeout=LIVE_TURN_TIMEOUT)
    except asyncio.TimeoutError:
        trace.error = f"no final response within {LIVE_TURN_TIMEOUT}s"

    trace.reasoning_full = "".join(reasoning)
    trace.tool_results = _maybe_truncate("\n".join(results), TOOL_RESULTS_TOTAL_CHARS)


def _estimate_tokens(trace: "LiveTrace") -> int:
    """Cheap 4-chars/token estimate over what the turn actually produced — no API call."""
    from src.core.utils.token_counter import TokenCounter
    text = "".join((trace.reasoning_full or "", trace.tool_results or "", trace.final_response or ""))
    return TokenCounter.count_text(text)


# ── Plan-progress helpers (shared by LiveTrace + probe_workflow) ─────────────


def _plan_progress(plan: Optional[List[Dict[str, Any]]]) -> tuple:
    """(completed, total) step counts from a plan snapshot."""
    if not plan:
        return (0, 0)
    total = len(plan)
    done = sum(1 for s in plan if isinstance(s, dict) and s.get("status") == "completed")
    return (done, total)


def _active_step_desc(plan: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """The first not-yet-done step — i.e. where execution is (or is stuck)."""
    if not plan:
        return None
    for s in plan:
        if isinstance(s, dict) and s.get("status") in ("pending", "running", "failed"):
            return f"{s.get('id')}: {str(s.get('description', ''))[:140]}"
    return None


def _detect_stall(rounds_detail: List[Dict[str, Any]]) -> Optional[str]:
    """Name the failure mode from the per-round trail, so a 'did not complete' isn't opaque.

    The two failure modes a plan-lane run actually hits: (1) it never marks ANY step complete
    (the model executes tools but forgets to call complete_step, so the plan never advances), or
    (2) it makes some progress then loops on one step without advancing. Both look like 'ran out
    of rounds' unless you inspect step progress across rounds — which is exactly what this does.
    """
    if not rounds_detail:
        return None
    last = rounds_detail[-1]
    if last.get("steps_total") and last.get("steps_completed", 0) == 0:
        return (f"0 of {last['steps_total']} steps ever marked complete across "
                f"{len(rounds_detail)} rounds — model executed tools but never called complete_step")
    tail = rounds_detail[-3:]
    if len(tail) >= 2 and len({r.get("steps_completed") for r in tail}) == 1:
        tools = tail[-1].get("tools") or []
        repeat = f" (repeating: {', '.join(sorted(set(tools)))})" if tools else ""
        return f"no step progress in the last {len(tail)} rounds{repeat} — likely looping"
    return None


# ── Concurrent / interleaved conversations (session-isolation regression lane) ──
#
# Issue #342 (refs #269): a genuine LIVE regression guard for the exact incident class in
# docs/architecture/SESSION_ISOLATION.md — a clarifying-question / approval card raised in one
# chat leaking onto ANOTHER chat because the approval message shipped without a `conversation_id`.
#
# Faithful model: the production web Limb multiplexes EVERY conversation over ONE websocket (the
# server keeps a single `active_websocket`; see limb_coordinator.py). Isolation is therefore NOT
# "one socket per chat" — it is "every turn message the server emits carries the `conversation_id`
# it belongs to, and a mismatch is dropped." So we drive several conversations, interleaved, over a
# SINGLE connection and check the server's per-event tagging directly — the server-side mirror of
# the client's `guardConversation` (sessionGuard.ts): a concrete `conversation_id` that doesn't
# match the conversation being driven is a leak; an untagged (`None`) payload is the legacy
# escape-hatch (`conversationMatches(null, …) === true`) and only counts as a leak for the
# human-facing pause payloads whose whole job is to be attributable (the approval card + the paused
# ai_response — the precise thing #269 fixed).


@dataclass
class ConcurrentPhase:
    """One driven conversation within a probe_concurrent run, and everything the server sent back
    while it was the in-flight conversation."""
    conversation_id: str
    prompt: str
    category: str
    events: List[Dict[str, Any]] = field(default_factory=list)   # {type, conversation_id, ...} in order
    approval: Optional[Dict[str, Any]] = None    # the tool_approval_request card seen (if any)
    terminal: Optional[Dict[str, Any]] = None    # the non-fragment ai_response that ended the phase
    timed_out: bool = False

    @property
    def paused(self) -> bool:
        t = self.terminal or {}
        return bool(t.get("awaiting_approval") or t.get("awaiting_input"))


@dataclass
class ConcurrentTrace:
    phases: List[ConcurrentPhase] = field(default_factory=list)
    leaks: List[str] = field(default_factory=list)      # human-readable cross-conversation violations
    error: Optional[str] = None
    duration_s: float = 0.0

    @property
    def isolated(self) -> bool:
        return not self.leaks and not self.error


def _detect_isolation_leaks(phases: List[ConcurrentPhase]) -> List[str]:
    """The assertion: no live turn state crosses `conversation_id` boundaries.

    Two independent violation classes, both mirroring sessionGuard.ts semantics so this is a real
    server-side guard, not a looser re-check:
      1. Cross-conversation bleed — an event drained during conversation X's turn (each turn is
         fully drained to its terminal ai_response BEFORE the next is driven) carries a CONCRETE
         `conversation_id` that is not X. With sequential draining X's stream is its own turn's
         output, so a foreign concrete tag means the server emitted another conversation's turn
         state onto the shared socket inside X's window. If that foreign event is in fact X-owned
         content MIS-stamped with another id, the client applies it to the wrong chat — the leak.
      2. Untagged pause payload — a `tool_approval_request` (clarifying-question card) or a paused
         `ai_response` shipped with NO `conversation_id`. `null` is the legacy allow-path everywhere
         else (`conversationMatches(null, …) === true`), but for these human-facing pause payloads
         that is precisely the #269 bug: an untagged card falls through every downstream guard's
         "untagged => allow" branch and lands on whatever chat is on screen. Emitters of pause state
         MUST tag concretely — this is the class the untagged-card `--self-test` case reproduces.
    """
    leaks: List[str] = []
    for ph in phases:
        conv = ph.conversation_id
        for ev in ph.events:
            cid = ev.get("conversation_id")
            if cid is not None and cid != conv:
                leaks.append(
                    f"[{conv}] a '{ev['type']}' event tagged for a DIFFERENT conversation "
                    f"({cid}) arrived inside {conv}'s turn window — cross-conversation bleed on the "
                    f"shared socket (turns are drained sequentially, so this should never happen)"
                )
        if ph.approval is not None and ph.approval.get("conversation_id") is None:
            leaks.append(
                f"[{conv}] tool_approval_request (clarifying-question card) shipped WITHOUT a "
                f"conversation_id — untagged card falls through the client's legacy allow-path and "
                f"leaks onto whatever chat is on screen (this IS the #269 regression)"
            )
        term = ph.terminal
        if term and (term.get("awaiting_approval") or term.get("awaiting_input")) \
                and term.get("conversation_id") is None:
            leaks.append(
                f"[{conv}] paused ai_response (stop_reason={term.get('stop_reason')}) shipped "
                f"WITHOUT a conversation_id — the paused turn cannot be attributed to its chat "
                f"(#269 regression class)"
            )
    return leaks


async def _collect_isolation_phase(ws, timeout: float) -> Dict[str, Any]:
    """Drain server events for ONE driven turn, recording each event's `conversation_id` tag, until
    a terminal (non-fragment) ai_response. Unlike `_collect` this does not interpret the turn — it
    keeps the raw per-event conversation tagging so the isolation check can see exactly what the
    server attributed to whom. Returns {events, approval, terminal, timed_out}."""
    events: List[Dict[str, Any]] = []
    approval: Optional[Dict[str, Any]] = None
    deadline = time.time() + timeout
    idle_checks = 0
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return {"events": events, "approval": approval, "terminal": None, "timed_out": True}
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, ROUND_IDLE_CHECK_S))
        except asyncio.TimeoutError:
            idle_checks += 1
            if idle_checks >= MAX_IDLE_CHECKS:
                return {"events": events, "approval": approval, "terminal": None, "timed_out": True}
            continue
        idle_checks = 0
        evt = json.loads(raw)
        etype = evt.get("type", "")
        p = evt.get("payload", {}) or {}
        rec: Dict[str, Any] = {"type": etype, "conversation_id": p.get("conversation_id")}
        if etype == "tool_approval_request":
            rec["token"] = p.get("token")
            rec["tool_name"] = p.get("tool_name")
            approval = rec
        events.append(rec)
        if etype == "ai_response":
            if p.get("fragment", False):
                continue      # streaming chunk — recorded above (still leak-checked), not terminal
            rec["awaiting_approval"] = bool(p.get("awaiting_approval"))
            rec["awaiting_input"] = bool(p.get("awaiting_input"))
            rec["stop_reason"] = p.get("stop_reason")
            return {"events": events, "approval": approval, "terminal": rec, "timed_out": False}
        if etype in ("error", "system_error"):
            rec["stop_reason"] = "error"
            return {"events": events, "approval": approval, "terminal": rec, "timed_out": False}


async def probe_concurrent(steps: List[Dict[str, Any]], *,
                           auth_token: Optional[str] = None,
                           phase_timeout: Optional[float] = None) -> ConcurrentTrace:
    """Drive several conversations, interleaved, over ONE websocket and check for session leakage.

    `steps` is an ordered list of ``{"conv": <conversation_id>, "prompt": <text>,
    "category": "agent"|"planning"}`` (category defaults to "agent"). Each step sends a fresh
    user_input on ITS conversation_id and drains events to that turn's natural stop — a pause
    (awaiting_approval / awaiting_input, e.g. a clarifying-question card) or a completion. Pauses are
    deliberately NOT resolved, so a step that pauses leaves that conversation with in-flight state
    on the server while the NEXT step drives a different conversation — reproducing the exact
    overlapping-in-flight-state condition of the #269 incident.

    Returns a `ConcurrentTrace` whose `.leaks` is empty iff isolation held (see
    `_detect_isolation_leaks`). Requires a running Brain (start one from this worktree to test your
    edit rather than prod — see the module docstring / the eval-harness skill)."""
    import websockets

    trace = ConcurrentTrace()
    token = auth_token or load_auth_token()
    if not token:
        trace.error = "no auth token (set CHAD_REMOTE_TOKEN or add it to .env)"
        return trace

    timeout = phase_timeout if phase_timeout is not None else LIVE_TURN_TIMEOUT
    headers = {"Origin": "http://localhost:5173"}
    t0 = time.time()
    try:
        try:
            ws_ctx = websockets.connect(SERVER_WS, additional_headers=headers,
                                        ping_interval=None, open_timeout=30)
        except TypeError:
            ws_ctx = websockets.connect(SERVER_WS, extra_headers=headers,
                                        ping_interval=None, open_timeout=30)

        async with ws_ctx as ws:
            await ws.send(_msg("auth", auth_token=token))
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=35.0))
            if reply.get("type") != "auth" or reply.get("payload", {}).get("status") != "success":
                trace.error = f"auth rejected: {reply.get('payload')}"
                return trace

            for step in steps:
                conv = step["conv"]
                prompt = step["prompt"]
                category = step.get("category", "agent")
                await ws.send(_msg("user_input", {
                    "text": prompt, "initiator": "user",
                    "conversation_id": conv, "category": category, "platform": EVAL_PLATFORM,
                }, auth_token=token))
                res = await _collect_isolation_phase(ws, timeout)
                phase = ConcurrentPhase(
                    conversation_id=conv, prompt=prompt, category=category,
                    events=res["events"], approval=res["approval"],
                    terminal=res["terminal"], timed_out=res["timed_out"],
                )
                trace.phases.append(phase)
                if res["timed_out"]:
                    trace.error = trace.error or (
                        f"phase driving {conv} timed out with no terminal ai_response "
                        f"({len(res['events'])} events seen)"
                    )
                    break
    except Exception as e:  # noqa: BLE001
        trace.error = f"{type(e).__name__}: {e}"

    trace.leaks = _detect_isolation_leaks(trace.phases)
    trace.duration_s = round(time.time() - t0, 1)
    return trace


# ── Full workflow: plan → approve → execute until complete (or fails) ────────

from evals.core import PAUSE_REASONS  # noqa: E402 — single source of truth, see core.py


def _evaluate_completion_check(check: Any, agg: "LiveTrace") -> tuple[bool, str]:
    """Run a caller's completion predicate. Returns ``(satisfied, gap_description)``.

    Accepts either ``() -> bool`` or ``() -> (bool, str)``; the string form lets the caller
    name the gap so the nudge can be specific about what is missing rather than generic.

    A predicate that raises is treated as **satisfied**. Deciding "the model failed" on the
    strength of our own broken check would manufacture findings — the opposite of what this
    is for. The exception surfaces on the trace instead.
    """
    try:
        outcome = check()
    except Exception as exc:  # noqa: BLE001
        agg.rounds_detail and agg.rounds_detail[-1].update(
            {"completion_check_error": f"{type(exc).__name__}: {exc}"}
        )
        return True, ""
    if isinstance(outcome, tuple):
        satisfied, gap = bool(outcome[0]), str(outcome[1] if len(outcome) > 1 else "")
    else:
        satisfied, gap = bool(outcome), ""
    if satisfied:
        return True, ""
    # Never hand an empty gap to the nudge template — a nudge that names nothing specific
    # is the generic "be thorough" prompting that measurably does not work.
    return False, gap or "the expected change is not present"


async def probe_workflow(prompt: str, *, category: str = "planning",
                         approval: str = WORKFLOW_APPROVAL_ANSWER, max_rounds: int = 6,
                         conv_id: Optional[str] = None,
                         auth_token: Optional[str] = None,
                         interactive: bool = False,
                         resume_token: Optional[str] = None,
                         resume_answer: Optional[str] = None,
                         completion_check: Optional[Callable[[], Any]] = None,
                         ws=None) -> LiveTrace:
    """Drive a full multi-turn workflow over ONE WS connection.

    Round 1 sends the prompt (or, when resuming — see `resume_token` below — resolves the
    pending question instead); the planner proposes a plan and the graph pauses
    (stop_reason == "awaiting_approval"). We then send `approval` ("proceed") on the
    SAME conversation_id, which resumes the graph past approval_wait into execution.
    We keep advancing on every pause until the turn ends without one (completed) or
    we hit `max_rounds`. Executed tools / reasoning / results accumulate across rounds;
    the plan is the first one proposed, the response is the final turn's.

    Pass ``ws`` from ``open_eval_ws`` to reuse a suite-level connection (auth once).

    interactive=True changes how an approval-gated interactive tool mid-run (e.g.
    ask_user_question) is handled: instead of auto-answering with a canned "use your best
    judgment" reply, the run STOPS and returns immediately with `awaiting_human=True` plus
    the actual question content (`pending_approval_prompt`/`_title`/`_questions`) so a real
    caller (a human, or an agent driving the eval) can decide a real answer instead of a
    generic default. Resume the SAME workflow by calling `probe_workflow` again with the
    same `conv_id`, `resume_token=<the returned pending_approval_token>`, and
    `resume_answer=<the answer text>` — approval state lives server-side keyed by token
    (see approval_store.py), not by WS connection, so a fresh connection can resolve it.
    Plan-approval pauses (no token — just "start executing") always auto-advance with
    `approval` regardless of `interactive`, since that isn't a question to answer.

    ``completion_check`` is an optional predicate over OBSERVABLE STATE — `() -> bool` or
    `() -> (bool, gap_description)` — asked after any turn that ends without a pause. It
    exists for lanes with no plan to measure against (the agent lane, #566): `code_agent`
    would read the target file and end with "Task completed." having produced no diff, and
    the plan-progress nudge could never fire because there was no plan. coder_bench passes
    "is `git diff` non-empty in the worktree". An unsatisfied predicate nudges (bounded by
    MAX_CONTINUE_NUDGES) and, at the end, overrides `completed` — the model's own claim to
    have finished is not evidence that it did.
    """
    if resume_token and not conv_id:
        raise ValueError("resume_token requires conv_id (the conversation being resumed)")

    agg = LiveTrace(prompt=prompt, category=category, rounds=0)
    token = auth_token or load_auth_token()
    if not token:
        agg.error = "no auth token (set CHAD_REMOTE_TOKEN or add it to .env)"
        return agg

    conv = conv_id or f"eval_wf_{uuid.uuid4().hex[:8]}"
    agg.session_id = conv          # carried into the report so we can pull the full LangSmith trace
    t0 = time.time()
    reasoning_all: List[str] = []
    results_all: List[str] = []
    try:
        if ws is not None:
            await _probe_workflow_on_ws(
                ws, agg, prompt, category, approval, max_rounds, conv, token,
                interactive, resume_token, resume_answer, reasoning_all, results_all,
                completion_check,
            )
        else:
            async with open_eval_ws(token) as owned:
                await _probe_workflow_on_ws(
                    owned, agg, prompt, category, approval, max_rounds, conv, token,
                    interactive, resume_token, resume_answer, reasoning_all, results_all,
                    completion_check,
                )
    except Exception as e:
        if not agg.error:
            agg.error = f"{type(e).__name__}: {e}"

    agg.reasoning_full = "".join(reasoning_all)
    agg.tool_results = _maybe_truncate("\n".join(results_all), TOOL_RESULTS_TOTAL_CHARS)
    agg.duration_s = round(time.time() - t0, 1)
    agg.total_tokens = _estimate_tokens(agg)
    return agg


async def _probe_workflow_on_ws(
    ws, agg: LiveTrace, prompt: str, category: str, approval: str, max_rounds: int,
    conv: str, token: str, interactive: bool, resume_token: Optional[str],
    resume_answer: Optional[str], reasoning_all: List[str], results_all: List[str],
    completion_check: Optional[Callable[[], Any]] = None,
) -> None:
    await drain_ws(ws)
    text = prompt
    continue_nudges = 0
    first_round = True
    for rnd in range(1, max_rounds + 1):
        agg.rounds = rnd
        if first_round and resume_token:
            # Resuming a paused conversation: resolve the pending question instead of
            # sending a fresh user_input. Same wire message probe_workflow's own
            # auto-answer path sends (below) — just with a caller-supplied decision.
            await ws.send(_msg("tool_approval_response", {
                "token": resume_token,
                "decision": resume_answer or "",
                "conversation_id": conv,
            }, auth_token=token))
            # The server only records the decision above. Like the normal Limb
            # and the auto-answer path below, start a follow-up turn explicitly.
            await ws.send(_msg("user_input", {
                "text": resume_answer or "", "initiator": "user",
                "conversation_id": conv, "category": category, "platform": EVAL_PLATFORM,
            }, auth_token=token))
        else:
            # platform="eval" — exclusive interactive priority; see EVAL_PLATFORM.
            await ws.send(_msg("user_input", {
                "text": text, "initiator": "user",
                "conversation_id": conv, "category": category, "platform": EVAL_PLATFORM,
            }, auth_token=token))
        first_round = False

        rt = LiveTrace(prompt=prompt, category=category)
        reasoning: List[str] = []
        results: List[str] = []
        try:
            await asyncio.wait_for(_collect(ws, rt, reasoning, results, expected_conv=conv),
                                   timeout=RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            agg.error = f"round {rnd}: no turn-end within {RESPONSE_TIMEOUT}s"
            rt.error = agg.error
            rt.stop_reason = "wall_timeout"

        agg.tool_names.extend(rt.tool_names)
        agg.tool_calls.extend(rt.tool_calls)
        agg.events.extend(rt.events)
        agg.timeline.extend(rt.timeline)
        reasoning_all.extend(reasoning)
        results_all.extend(results)
        if rt.plan and not agg.plan:
            agg.plan = rt.plan            # FIRST proposal (intended decomposition)
        if rt.plan:
            agg.final_plan = rt.plan      # LAST snapshot (actual step statuses)
        if rt.goal and not agg.goal:
            agg.goal = rt.goal
        agg.final_response = rt.final_response
        agg.stop_reason = rt.stop_reason
        agg.status = rt.status

        # Per-round progress trail — what THIS round did and how far the plan advanced.
        # This is the ledger that turns "ran out of rounds" into "completed 2/8, looped on step 3".
        done, total = _plan_progress(rt.plan or agg.final_plan)
        agg.rounds_detail.append({
            "round": rnd,
            "tools": list(rt.tool_names),
            "steps_completed": done,
            "steps_total": total,
            "active_step": _active_step_desc(rt.plan or agg.final_plan),
            "stop_reason": rt.stop_reason,
            "response_chars": len(rt.final_response or ""),
        })

        # LIVE observability: the WS stream only carries top-level turn-completes, so a
        # round that ran 4 subagents / 80 searches inside nested subgraphs looks empty here.
        # Pull the REAL nested activity from LangSmith after each round and log a one-liner
        # (+ store it on the round) so progress is visible AS IT HAPPENS — no post-hoc
        # manual LangSmith query. Threaded so the sync SDK call doesn't block the event loop;
        # best-effort (the trace lags a little and auth may be absent). Disable via
        # LANGSMITH_LIVE=0.
        if os.environ.get("LANGSMITH_LIVE", "1") not in ("0", "false", "no"):
            try:
                from evals.langsmith_trace import fetch_session_trace, render_trace_oneline
                s = await asyncio.to_thread(fetch_session_trace, conv)
                line = render_trace_oneline(s)
                agg.rounds_detail[-1]["langsmith"] = line
                print(f"  [round {rnd}] steps={done}/{total} tools={len(rt.tool_names)} "
                      f"stop={rt.stop_reason} | {line}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  [round {rnd}] langsmith live fetch failed: {e}", flush=True)

        if rt.error:
            agg.error = rt.error
            break

        if rt.stop_reason in PAUSE_REASONS:
            # An approval-gated interactive tool (ask_user_question) leaves a token that
            # MUST be resolved before the tool will re-run — resolving it marks the
            # approval_store entry answered so the gate passes on re-invocation. The plan
            # approval pause carries no token and just needs a "proceed" follow-up.
            if rt.pending_approval_token:
                if interactive:
                    # Hand control back to the caller instead of auto-answering. Copy the
                    # question content onto agg (the returned trace) and stop — the caller
                    # resumes with a real answer via resume_token/resume_answer.
                    agg.awaiting_human = True
                    agg.pending_approval_token = rt.pending_approval_token
                    agg.pending_approval_tool = rt.pending_approval_tool
                    agg.pending_approval_prompt = rt.pending_approval_prompt
                    agg.pending_approval_title = rt.pending_approval_title
                    agg.pending_approval_questions = rt.pending_approval_questions
                    agg.status = "success"
                    return
                decision_answer = (
                    WORKFLOW_QUESTION_ANSWER
                    if not (agg.plan or rt.plan)
                    else WORKFLOW_APPROVAL_ANSWER
                )
                await ws.send(_msg("tool_approval_response", {
                    "token": rt.pending_approval_token,
                    "decision": decision_answer,
                    "conversation_id": conv,
                }, auth_token=token))
                agg.rounds_detail[-1]["auto_answered"] = (
                    f"{rt.pending_approval_tool or 'approval'} (token {rt.pending_approval_token})"
                )
                text = decision_answer   # follow-up turn = the user's answer
            else:
                # Plan approval pause (no token).
                # Guard against false approvals when no plan was proposed!
                current_plan = agg.plan or rt.plan
                if not current_plan:
                    text = (
                        f"You paused for plan approval, but no plan was proposed. "
                        f"Formulate and propose the execution plan for: {prompt}"
                    )
                else:
                    # Anchor the approval to the specific goal / prompt
                    goal_anchor = (agg.goal or prompt[:120]).strip().replace("\n", " ")
                    text = (
                        f"Approved — execute the existing plan for '{goal_anchor}' now. Do not create a new plan. "
                        f"Call the tools/delegates for each step and call complete_step as you finish. "
                        f"Use your best judgment with reasonable defaults; do not pause to ask further "
                        f"questions unless you are truly blocked."
                    )
            continue

        # Turn ended WITHOUT a pause. That is only a real completion if the plan actually
        # advanced to done (or there was no plan). If steps remain, the model yielded early
        # with the plan unfinished — nudge it to continue, as a cooperative user would, but
        # bounded. Needing nudges is itself the reportable autonomy/anchoring deficit.
        done, total = _plan_progress(agg.final_plan or agg.plan)
        if total and done < total and continue_nudges < MAX_CONTINUE_NUDGES:
            continue_nudges += 1
            agg.continue_nudges = continue_nudges
            agg.rounds_detail[-1]["nudged_continue"] = continue_nudges
            active_step = _active_step_desc(agg.final_plan or agg.plan)
            active_info = f" (current open step: {active_step})" if active_step else ""
            text = (
                f"Continue executing the existing plan{active_info} unless it is completely done, "
                f"and if it is done, then emit `complete_step`. Do not create a new plan. "
                f"Do not just describe or narrate what you did; emit the necessary tool calls to advance, "
                f"or call `complete_step` if the work is finished."
            )
            continue

        # Same idea for lanes with no plan to measure against (agent lane, #566): ask the
        # caller's predicate whether the observable end state is actually there. The gap is
        # named in the nudge — a specific, checkable gap moves models where a generic
        # "be thorough" does not.
        if completion_check is not None and continue_nudges < MAX_CONTINUE_NUDGES:
            satisfied, gap = _evaluate_completion_check(completion_check, agg)
            if not satisfied:
                continue_nudges += 1
                agg.continue_nudges = continue_nudges
                agg.rounds_detail[-1]["nudged_completion"] = gap
                text = COMPLETION_NUDGE.format(gap=gap)
                continue

        agg.continue_nudges = continue_nudges
        agg.completed = (not total) or (done >= total)
        if completion_check is not None:
            satisfied, gap = _evaluate_completion_check(completion_check, agg)
            agg.completion_gap = None if satisfied else gap
            # An unsatisfied predicate overrides a "completed" verdict: the whole point is
            # that the model saying it finished is not evidence that it did.
            #
            # Deliberately does NOT set `agg.error`. `error` means "the probe itself
            # failed" — coder_bench's score() maps any error straight to `backend-error`,
            # which reads as infrastructure trouble and would mask the far more useful
            # `no-op` / "empty diff — no edit attempted" bucket it already computes from
            # the diff. The model doing nothing is a RESULT, not a harness failure. Caught
            # by running the bench: the first version of this reported a clean no-op as
            # backend-error. `completed=False` + `completion_gap` carry the signal.
            agg.completed = agg.completed and satisfied
        if not agg.completed and total:
            # Plan-progress diagnosis only applies when there WAS a plan; with total == 0
            # this used to render as a nonsensical "plan unfinished (0/0 steps)".
            stall = _detect_stall(agg.rounds_detail)
            agg.error = agg.error or (
                f"model ended its turn with the plan unfinished ({done}/{total} steps) "
                f"after {continue_nudges} continue-nudge(s) — it does not drive the plan "
                f"autonomously (ends turns with progress narration instead of tool calls)"
                + (f"; {stall}" if stall else "")
            )
        break
    else:
        # for-loop exhausted without break — hit max_rounds
        done, total = _plan_progress(agg.final_plan or agg.plan)
        active = _active_step_desc(agg.final_plan or agg.plan)
        stall = _detect_stall(agg.rounds_detail)
        agg.error = (
            f"workflow did not complete within {max_rounds} rounds "
            f"(last stop_reason={agg.stop_reason}); progress={done}/{total} steps completed"
            + (f"; stuck on {active}" if active else "")
            + (f"; {stall}" if stall else "")
        )


async def _collect(ws, trace: LiveTrace, reasoning: List[str], results: List[str],
                    expected_conv: Optional[str] = None) -> None:
    """Drain events until the final ai_response arrives.

    Idle-based stall detection: if no event arrives for ``ROUND_IDLE_CHECK_S`` for
    ``MAX_IDLE_CHECKS`` consecutive checks, the turn is treated as stalled (not merely slow) and
    we return with a diagnostic error rather than blocking on a silent socket forever."""
    last_progress = time.monotonic()
    last_signature = None
    while True:
        try:
            remaining = ROUND_IDLE_CHECK_S * MAX_IDLE_CHECKS - (time.monotonic() - last_progress)
            if remaining <= 0:
                raise asyncio.TimeoutError
            raw = await asyncio.wait_for(ws.recv(), timeout=min(ROUND_IDLE_CHECK_S, remaining))
        except asyncio.TimeoutError:
            if time.monotonic() - last_progress >= ROUND_IDLE_CHECK_S * MAX_IDLE_CHECKS:
                silent_s = ROUND_IDLE_CHECK_S * MAX_IDLE_CHECKS
                last_tool = trace.tool_names[-1] if trace.tool_names else "none"
                trace.error = (f"stalled: no task progress for {silent_s}s "
                               f"(last tool={last_tool}, {len(trace.tool_names)} tools so far)")
                trace.stop_reason = trace.stop_reason or "stalled"
                return
            continue
        evt = json.loads(raw)
        etype = evt.get("type", "")
        p = evt.get("payload", {})

        # Drop events tagged for a different conversation on the shared socket
        msg_conv = p.get("conversation_id")
        if expected_conv and msg_conv and msg_conv != expected_conv:
            continue

        # Only task activity extends the idle deadline. Heartbeats, other chats,
        # empty thoughts and identical snapshots must not conceal a stalled turn.
        state = p.get("state")
        meaningful = (etype in {"ai_response", "tool_approval_request", "execution_plan", "error", "system_error"}
                      or (etype == "ai_thought" and
                          (state in {"tool_start", "tool_end"} or
                           state in {"reasoning", "thinking", "thought", "thought_reasoning"} and bool(p.get("text")))))
        signature = json.dumps([etype, {k: p.get(k) for k in
            ("state", "text", "name", "params", "result", "content", "plan", "token")}], sort_keys=True)
        if meaningful and (signature != last_signature or
                           etype == "ai_thought" and state not in {"tool_start", "tool_end"} and p.get("append", True)):
            last_progress = time.monotonic()
            last_signature = signature

        if etype == "tool_approval_request":
            # An interactive/approval-gated tool (ask_user_question, etc.) paused the graph.
            # Capture the token (so probe_workflow can auto-answer it) AND the actual human-facing
            # content (prompt/title/questions — see approval_gate.py's payload shape) so the
            # interactive lane can show the caller what's really being asked instead of a bare
            # tool name. The terminal ai_response (sent right after this card) carries
            # awaiting_input and ends the round normally.
            trace.pending_approval_token = p.get("token")
            trace.pending_approval_tool = p.get("tool_name")
            trace.pending_approval_prompt = p.get("prompt")
            trace.pending_approval_title = p.get("title")
            trace.pending_approval_questions = p.get("questions")
            continue

        if etype == "ai_response":
            if p.get("fragment", False):
                continue
            trace.final_response = p.get("text", "")
            # Prefer explicit pause flags; fall back to stop_reason for older servers.
            if p.get("awaiting_approval"):
                trace.stop_reason = "awaiting_approval"
            elif p.get("awaiting_input"):
                trace.stop_reason = "awaiting_input"
            else:
                trace.stop_reason = p.get("stop_reason")
            trace.status = "failure" if trace.stop_reason == "error" else "success"
            if trace.stop_reason == "error":
                # error_detail (added to the ai_response payload by response_processor.py)
                # carries the actual provider error text (e.g. LM Studio's "Context size has
                # been exceeded") -- prefer it over the generic honest-fallback response text
                # so the eval report/transcript shows the real cause, not just "an error occurred".
                trace.error = (p.get("error_detail") or trace.final_response
                              or "turn ended with stop_reason=error")
            return

        if etype == "execution_plan":
            plan = p.get("plan")
            if isinstance(plan, list):
                trace.plan = plan            # latest snapshot within THIS round (statuses evolve)
            trace.timeline.append("plan")
            trace.events.append({
                "type": "plan",
                "plan": plan,
                "goal": p.get("goal"),
                "gather_brief": (
                    p.get("gather_brief")
                    or (p.get("content") or {}).get("gather_brief")
                ),
            })
            trace.goal = trace.goal or p.get("goal")
            trace.gather_brief = (
                p.get("gather_brief")
                or (p.get("content") or {}).get("gather_brief")
            )

        elif etype == "ai_thought":
            state = p.get("state", "")
            if state in ("reasoning", "thinking", "thought", "thought_reasoning"):
                agent = p.get("agent_name") or "Main"
                text = p.get("text", "")
                # Default append=True matches the Brain wire format (streaming deltas).
                append = bool(p.get("append", True))
                _record_thinking(
                    trace, reasoning, agent=agent, text=text, state=state, append=append,
                )
            elif state == "tool_start":
                name = p.get("name", "?")
                params = p.get("params") or {}
                trace.timeline.append(f"tool:{name}")
                trace.tool_names.append(name)
                trace.tool_calls.append({"name": name, "params": params})
                trace.events.append({"type": "tool_start", "name": name, "params": params})
                print(f"    -> tool: {name}", flush=True)
            elif state == "tool_end":
                # Prefer full bodies (content / gather_brief) over UI summary `result`.
                name = p.get("name", "?")
                body = p.get("content")
                if body in (None, ""):
                    body = p.get("gather_brief")
                if body in (None, ""):
                    body = p.get("result", "")
                result_text = _maybe_truncate(str(body), TOOL_RESULT_CHARS)
                results.append(f"{name} -> {result_text}")
                ev_end: Dict[str, Any] = {"type": "tool_end", "name": name, "result": result_text}
                if p.get("content") not in (None, ""):
                    ev_end["content"] = p.get("content")
                if p.get("gather_brief") not in (None, ""):
                    ev_end["gather_brief"] = p.get("gather_brief")
                if p.get("brief") not in (None, ""):
                    ev_end["brief"] = p.get("brief")
                if p.get("steps") is not None:
                    ev_end["steps"] = p.get("steps")
                trace.events.append(ev_end)

        elif etype in ("error", "system_error"):
            trace.error = p.get("error", str(p))
            return
