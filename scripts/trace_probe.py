"""
trace_probe.py — Autonomous Pipeline Probe
==========================================
Sends a prompt (or a multi-turn plan) to the running Chad Brain server,
captures every event (including reasoning tokens), then pulls the LangSmith trace.

Designed to fail LOUD at each step — never assume silence = working.

Zero project imports. Runs with any Python that has websockets + langsmith.

Usage:
    # Single turn
    python scripts/trace_probe.py
    python scripts/trace_probe.py --prompt "what time is it?"
    python scripts/trace_probe.py --stop-after 3   # only run steps 1-3
    python scripts/trace_probe.py --no-langsmith    # skip LangSmith step

    # Multi-turn plan (keeps WebSocket open, shares conv_id across turns)
    python scripts/trace_probe.py --plan "Draft a professional email to my manager" "Save it as a draft" "Remind me to send it tomorrow at 9am"

    # Planner→Agent mode (interactive: approve plan before agent executes)
    python scripts/trace_probe.py --planner "Plan how to back up my documents folder"
    python scripts/trace_probe.py --planner "Plan a DB migration" --agent-prompt "Execute the migration plan"

Steps (single turn):
    1. Load auth token
    2. Check server /health
    3. Check LM Studio (warn only, not fatal)
    4. WebSocket auth handshake
    5. Send prompt, verify first event arrives
    6. Collect full response (captures reasoning tokens from stream)
    7. Save local trace JSON
    8. Pull LangSmith trace (optional)

Steps (multi-turn plan):
    Same 1-3, then for each turn in the plan:
    4. WebSocket auth handshake (once, connection held open)
    5-6. Send turn N prompt, collect full response
    7. Save per-turn + combined plan JSON
    8. Pull LangSmith trace per turn (optional)

Steps (planner→agent):
    Same 1-3, then:
    4-6. Planner node — send planning prompt, collect plan response
    [interactive] Show plan, prompt user to approve or abort
    4-6. Agent node  — send agent-prompt over same conv_id, collect response
    7. Save planner + agent traces + combined JSON
    8. Pull LangSmith trace per node (optional)
"""

import asyncio
import json
import os
import sys
import time
import uuid
import argparse
import urllib.request
import urllib.error
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 so emoji in model responses don't crash on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ────────────────────────────────────────────────────────────────

SERVER_URL   = os.getenv("CHAD_SERVER_URL", "ws://localhost:8767/ws")
HEALTH_URL   = os.getenv("CHAD_SERVER_HEALTH", "http://localhost:8767/health")
LM_URL       = "http://localhost:1234/v1"
RESPONSE_TIMEOUT = int(os.getenv("CHAD_PROBE_TIMEOUT", "600"))   # seconds for full AI response (allow model swaps)
FIRST_EVENT_TIMEOUT = 60  # seconds to wait for the FIRST event after sending prompt

ANSI = {
    "ok":    "\033[92m",
    "warn":  "\033[93m",
    "err":   "\033[91m",
    "info":  "\033[96m",
    "reset": "\033[0m",
    "bold":  "\033[1m",
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def ts() -> str:
    return time.strftime("%H:%M:%S")

def step(n, label: str) -> None:
    print(f"\n{ANSI['bold']}{ANSI['info']}-- STEP {n}: {label} --{ANSI['reset']}")

def ok(msg: str) -> None:
    print(f"  {ANSI['ok']}[{ts()}] OK   {ANSI['reset']} {msg}")

def warn(msg: str) -> None:
    print(f"  {ANSI['warn']}[{ts()}] WARN {ANSI['reset']} {msg}")

def info(msg: str) -> None:
    print(f"  {ANSI['info']}[{ts()}] INFO {ANSI['reset']} {msg}")

def fail(step_n, msg: str, hint: str = "") -> None:
    print(f"\n  {ANSI['err']}{ANSI['bold']}[{ts()}] STEP {step_n} FAILED{ANSI['reset']}")
    print(f"  {ANSI['err']}Reason : {msg}{ANSI['reset']}")
    if hint:
        print(f"  {ANSI['warn']}Fix    : {hint}{ANSI['reset']}")
    sys.exit(step_n)

# ── Step 1: Load auth token ──────────────────────────────────────────────────

def load_auth_token(project_root: Path) -> str:
    step(1, "Load auth token")

    # 1a. Environment variable (either name)
    for key in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            ok(f"Token loaded from env var {key}")
            return val

    # 1b. Parse .env — search from project_root upward (worktrees don't have their own .env)
    dotenv_path = None
    candidate = project_root
    for _ in range(6):  # walk up at most 6 levels
        if (candidate / ".env").exists():
            dotenv_path = candidate / ".env"
            break
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    if dotenv_path and dotenv_path.exists():
        info(f"Parsing {dotenv_path}")
        with open(dotenv_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN") and v:
                    ok(f"Token loaded from .env ({k})")
                    return v
    else:
        warn(".env not found anywhere up to drive root")

    # 1c. Try secrets.yaml (simple grep, no yaml import required)
    secrets_path = None
    candidate = project_root
    for _ in range(6):
        if (candidate / "config" / "secrets.yaml").exists():
            secrets_path = candidate / "config" / "secrets.yaml"
            break
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    if secrets_path and secrets_path.exists():
        info(f"Scanning {secrets_path}")
        with open(secrets_path, encoding="utf-8") as f:
            for line in f:
                for key in ("chad_remote_token", "chad_server_token"):
                    if key in line.lower() and ":" in line:
                        v = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if v and v != "null":
                            ok(f"Token loaded from secrets.yaml ({key})")
                            return v

    fail(1,
         "No auth token found in env, .env, or config/secrets.yaml",
         "Set CHAD_REMOTE_TOKEN=<your_token> in your .env file")


# ── Step 2: Server health ────────────────────────────────────────────────────

def check_server_health() -> None:
    step(2, "Server health check")
    info(f"GET {HEALTH_URL}")
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5.0) as resp:
            if resp.status == 200:
                body = resp.read().decode()[:200]
                ok(f"Server is UP -- HTTP 200 | {body}")
                return
            fail(2,
                 f"Health endpoint returned HTTP {resp.status}",
                 "Check server logs for startup errors")
    except urllib.error.URLError as e:
        fail(2,
             f"Cannot reach {HEALTH_URL}: {e.reason}",
             "Start the server: $env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
             ".venv\\Scripts\\python.exe src\\remote\\server.py")
    except Exception as e:
        fail(2, f"Unexpected error hitting /health: {e}")


# ── Read local model config (no yaml import — simple grep) ──────────────────

def read_local_model_config(project_root: Path) -> dict:
    """Return {'use_local': bool, 'active_model': str|None} from config.yaml."""
    config_path = None
    candidate = project_root
    for _ in range(6):
        if (candidate / "config" / "config.yaml").exists():
            config_path = candidate / "config" / "config.yaml"
            break
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    result = {"use_local": False, "active_model": None}
    if not config_path:
        warn("config/config.yaml not found — assuming cloud model")
        return result

    in_local_models = False
    with open(config_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()
            if stripped == "local_models:":
                in_local_models = True
                continue
            if in_local_models:
                # Exit section when we hit a new top-level key
                if stripped and not stripped[0].isspace():
                    break
                if "use_local:" in stripped:
                    val = stripped.split("use_local:", 1)[1].strip().lower()
                    result["use_local"] = val in ("true", "yes", "1")
                elif "active_model:" in stripped:
                    val = stripped.split("active_model:", 1)[1].strip().strip('"').strip("'")
                    if val and val.lower() != "null":
                        result["active_model"] = val
    return result


# ── Step 3: LM Studio check (fatal when use_local=true) ─────────────────────

def check_lm_studio(local_cfg: dict) -> list:
    use_local   = local_cfg.get("use_local", False)
    active_model = local_cfg.get("active_model")
    label = "LM Studio check (FATAL — use_local=true)" if use_local else "LM Studio check (warn only)"
    step(3, label)

    try:
        with urllib.request.urlopen(f"{LM_URL}/models", timeout=3.0) as resp:
            data = json.loads(resp.read().decode())
            models = [m["id"] for m in data.get("data", [])]
            if not models:
                msg = "LM Studio is UP but no models are loaded"
                if use_local:
                    fail(3, msg, "Load a model in LM Studio before running the probe")
                else:
                    warn(msg)
                return []

            ok(f"LM Studio UP. Loaded models: {models}")

            if use_local and active_model:
                if active_model in models:
                    ok(f"Active model '{active_model}' is loaded ✓")
                else:
                    fail(3,
                         f"Active model '{active_model}' is NOT loaded in LM Studio.\n"
                         f"  Loaded: {models}",
                         f"Load '{active_model}' in LM Studio, or update "
                         f"local_models.active_model in config/config.yaml")
            return models

    except Exception as e:
        if use_local:
            fail(3,
                 f"LM Studio not reachable at {LM_URL}: {e}",
                 "Start LM Studio and enable the server (Developer → Enable Server), "
                 "or set local_models.use_local: false in config/config.yaml")
        else:
            warn(f"LM Studio not reachable at {LM_URL}: {e}")
            warn("Using cloud model — this is fine.")
            return []


# ── Protocol helpers (inlined -- no project imports) ──────────────────────────

def _make_msg(msg_type: str, payload: dict = None, auth_token: str = None) -> str:
    return json.dumps({
        "id": str(uuid.uuid4())[:8],
        "type": msg_type,
        "payload": payload or {},
        "auth_token": auth_token,
        "timestamp": time.time(),
    })


# ── Single turn: send prompt and collect full response ───────────────────────

async def _execute_turn(ws, prompt: str, trace_tag: str, conv_id: str,
                        turn_n: int, t0: float, category: str = "agent") -> dict:
    """
    Send one prompt over an already-authenticated WebSocket and collect the
    full response. Returns a per-turn trace dict.
    """
    def elapsed() -> str:
        return f"+{time.time() - t0:.1f}s"

    trace = {
        "trace_tag": trace_tag,
        "turn": turn_n,
        "prompt": prompt,
        "conv_id": conv_id,
        "started_at": time.time(),
        "first_event_at": None,
        "events": [],
        "reasoning_chunks": [],
        "tool_calls": [],
        "final_response": None,
        "status": "failure",
        "error": None,
    }

    label = f"Turn {turn_n}" if turn_n > 1 else "Send prompt"
    step(f"5{chr(ord('a') + turn_n - 1)}" if turn_n > 1 else 5,
         f"{label} + wait for first event (timeout {FIRST_EVENT_TIMEOUT}s)")

    tagged_prompt = f"{prompt} [PROBE:{trace_tag}]"
    user_msg = _make_msg("user_input", {
        "text": tagged_prompt,
        "initiator": "user",
        "conversation_id": conv_id,
        "category": category,
    })
    await ws.send(user_msg)
    info(f"[{elapsed()}] --> SENT user_input (turn {turn_n}) | conv_id={conv_id}")
    info(f"[{elapsed()}]     prompt: {tagged_prompt[:200]}")

    # Wait for FIRST event
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=FIRST_EVENT_TIMEOUT)
    except asyncio.TimeoutError:
        fail(5,
             f"No event received from server within {FIRST_EVENT_TIMEOUT}s after turn {turn_n}",
             "The message was sent but the server produced nothing. Likely causes:\n"
             "    * Server is still initializing (LangGraph, Supabase, tool registry)\n"
             "    * LM Studio model is not loaded\n"
             "    * Server threw an exception silently\n"
             "    * Check the server terminal for tracebacks")

    evt = json.loads(raw)
    trace["first_event_at"] = time.time() - t0
    trace["events"].append(evt)
    ok(f"[{elapsed()}] FIRST EVENT (turn {turn_n}): type={evt.get('type')} "
       f"payload={json.dumps(evt.get('payload', {}))[:200]}")

    # Collect full response
    step_label = f"6{chr(ord('a') + turn_n - 1)}" if turn_n > 1 else 6
    step(step_label, f"Collect full response for turn {turn_n} (timeout {RESPONSE_TIMEOUT}s)")

    deadline = time.time() + RESPONSE_TIMEOUT
    last_event_t = time.time()

    while time.time() < deadline:
        wait_left = deadline - time.time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, wait_left))
        except asyncio.TimeoutError:
            idle = round(time.time() - last_event_t, 1)
            info(f"[{elapsed()}] ... waiting (no event for {idle}s, turn {turn_n})")
            continue

        last_event_t = time.time()
        evt = json.loads(raw)
        etype = evt.get("type", "")
        epayload = evt.get("payload", {})
        trace["events"].append(evt)

        if etype == "ai_response":
            is_fragment = epayload.get("fragment", False)
            text = epayload.get("text", "")
            if is_fragment:
                # Intermediate streaming token from LangGraph -- keep collecting.
                # The real final response has fragment=False (or fragment absent).
                info(f"[{elapsed()}] ai_response FRAGMENT ({len(text)} chars) -- continuing...")
                continue
            trace["final_response"] = text
            trace["status"] = "success"
            ok(f"[{elapsed()}] AI_RESPONSE FINAL (turn {turn_n}) received ({len(text)} chars)")
            print(f"\n  Response preview: {text[:400]}")
            break

        elif etype == "ai_thought":
            state = epayload.get("state", "")
            if state in ("reasoning", "thinking"):
                chunk = epayload.get("text", "")
                trace["reasoning_chunks"].append(chunk)
                if chunk.strip().endswith((".", "!", "?", "\n")) or len(chunk) > 40:
                    full_so_far = "".join(trace["reasoning_chunks"])
                    info(f"[{elapsed()}] <REASONING t{turn_n}> {full_so_far[-120:]!r}")
            elif state == "tool_start":
                name = epayload.get("name", "?")
                trace["tool_calls"].append({"name": name, "turn": turn_n,
                                            "params": epayload.get("params", {})})
                info(f"[{elapsed()}] TOOL_START  {name} | params={json.dumps(epayload.get('params', {}))[:200]}")
            elif state == "tool_end":
                name = epayload.get("name", "?")
                info(f"[{elapsed()}] TOOL_END    {name} | success={epayload.get('success')} | "
                     f"result={str(epayload.get('result', ''))[:200]}")
            else:
                info(f"[{elapsed()}] AI_THOUGHT  [{state}] {json.dumps(epayload)[:200]}")

        elif etype in ("error", "system_error"):
            err = epayload.get("error", str(epayload))
            trace["error"] = err
            fail(6,
                 f"Server returned an error event on turn {turn_n}: {err}",
                 "Check server logs for the full traceback")

        else:
            info(f"[{elapsed()}] [{etype}] {json.dumps(epayload)[:200]}")

    if trace["status"] != "success":
        idle_since = round(time.time() - last_event_t, 1)
        fail(6,
             f"No ai_response after {RESPONSE_TIMEOUT}s on turn {turn_n}. "
             f"Last event was {idle_since}s ago.",
             f"Events received: {len(trace['events'])}. "
             f"Reasoning chunks: {len(trace['reasoning_chunks'])}. "
             f"Tool calls: {len(trace['tool_calls'])}. "
             f"Check if the model is hung or LM Studio crashed.")

    full_reasoning = "".join(trace["reasoning_chunks"])
    trace["reasoning_full"] = full_reasoning
    trace["reasoning_chars"] = len(full_reasoning)
    trace["duration_s"] = round(time.time() - trace["started_at"], 2)

    ok(f"Turn {turn_n} complete -- {trace['duration_s']}s | "
       f"{len(trace['events'])} events | "
       f"{trace['reasoning_chars']} reasoning chars | "
       f"{len(trace['tool_calls'])} tool calls")

    return trace


# ── Steps 4-6: WebSocket session (single or multi-turn) ──────────────────────

async def run_ws_session(auth_token: str, turns: list, conv_id: str) -> list:
    """
    Opens one WebSocket connection, authenticates once, then executes each
    turn in sequence over the same connection.

    turns: list of (prompt, trace_tag, category) tuples
    conv_id: shared conversation ID — Supabase accumulates history across turns
    Returns: list of per-turn trace dicts
    """
    try:
        import websockets
    except ImportError:
        fail(4, "websockets package not installed", "pip install websockets")

    t0 = time.time()

    def elapsed() -> str:
        return f"+{time.time() - t0:.1f}s"

    step(4, "WebSocket auth handshake")
    info(f"Connecting to {SERVER_URL}")
    info(f"Session conv_id: {conv_id}")

    try:
        headers = {"Origin": "http://localhost:5173"}
        try:
            ws_ctx = websockets.connect(
                SERVER_URL, additional_headers=headers,
                ping_interval=None, open_timeout=15
            )
        except TypeError:
            ws_ctx = websockets.connect(
                SERVER_URL, extra_headers=headers,
                ping_interval=None, open_timeout=15
            )

        async with ws_ctx as ws:
            ok(f"[{elapsed()}] WebSocket OPEN")

            await ws.send(_make_msg("auth", auth_token=auth_token))
            info(f"[{elapsed()}] --> SENT auth")

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=35.0)
            except asyncio.TimeoutError:
                fail(4,
                     "No AUTH response from server within 35s",
                     "Server held the connection but never completed bootstrap.\n"
                     "    Check server logs for 'Bootstrap successfully resolved'.\n"
                     "    Slow MCP servers (google-drive) can add 8-25s to startup.")

            reply = json.loads(raw)
            info(f"[{elapsed()}] <-- RECV type={reply.get('type')} payload={json.dumps(reply.get('payload', {}))[:200]}")

            if reply.get("type") != "auth" or reply.get("payload", {}).get("status") != "success":
                fail(4,
                     f"Auth rejected: {reply.get('payload')}",
                     "Check CHAD_REMOTE_TOKEN matches the server's CHAD_SERVER_TOKEN")

            ok(f"[{elapsed()}] AUTH SUCCESS")

            # Execute all turns over the same open connection
            turn_traces = []
            for turn_n, turn in enumerate(turns, start=1):
                prompt, trace_tag, category = turn if len(turn) == 3 else (*turn, "agent")
                if turn_n > 1:
                    info(f"\n[{elapsed()}] Pausing 2s between turns to let server settle...")
                    await asyncio.sleep(2)
                turn_trace = await _execute_turn(ws, prompt, trace_tag, conv_id, turn_n, t0, category)
                turn_traces.append(turn_trace)

            return turn_traces

    except ConnectionRefusedError:
        fail(4, f"Connection refused at {SERVER_URL}",
             "Server is not running. Start it first (Step 2 hint).")
    except Exception as e:
        fail(4, f"{type(e).__name__}: {e}")


# ── Step 7: Save local trace ──────────────────────────────────────────────────

def save_trace(trace: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    events = trace.get("events", [])
    summary = {k: v for k, v in trace.items() if k != "events"}
    summary["event_count"] = len(events)
    summary["event_types"] = [e.get("type") for e in events]
    summary["notable_events"] = [
        {"type": e.get("type"), "payload": e.get("payload")}
        for e in events
        if e.get("type") not in ("ai_thought",)
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    ok(f"Trace saved: {out_path}")


def save_plan_trace(turn_traces: list, plan_id: str, prompts: list, out_path: Path) -> None:
    """Save a combined multi-turn plan trace with cross-turn summary."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan_id": plan_id,
        "conv_id": turn_traces[0]["conv_id"] if turn_traces else None,
        "turn_count": len(turn_traces),
        "prompts": prompts,
        "total_duration_s": sum(t.get("duration_s", 0) for t in turn_traces),
        "total_tool_calls": sum(len(t.get("tool_calls", [])) for t in turn_traces),
        "total_reasoning_chars": sum(t.get("reasoning_chars", 0) for t in turn_traces),
        "all_statuses": [t.get("status") for t in turn_traces],
        "overall_status": "success" if all(t.get("status") == "success" for t in turn_traces) else "failure",
        "turns": [],
    }
    for t in turn_traces:
        plan["turns"].append({
            "turn": t["turn"],
            "trace_tag": t["trace_tag"],
            "prompt": t["prompt"],
            "status": t["status"],
            "duration_s": t.get("duration_s"),
            "reasoning_chars": t.get("reasoning_chars", 0),
            "tool_calls": t.get("tool_calls", []),
            "final_response": t.get("final_response"),
            "reasoning_full": t.get("reasoning_full", ""),
        })
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, default=str)
    ok(f"Plan trace saved: {out_path}")


# ── Step 8: Pull LangSmith trace ──────────────────────────────────────────────

def _load_langsmith_key(project_root: Path) -> str:
    key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if key:
        return key
    candidate = project_root
    for _ in range(6):
        dotenv = candidate / ".env"
        if dotenv.exists():
            with open(dotenv, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY") and v:
                        os.environ[k] = v
                        return v
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return ""


def pull_langsmith_trace(trace_tag: str, project_root: Path,
                         ingestion_wait: float = 8.0) -> str | None:
    """
    Query LangSmith for a run matching PROBE:trace_tag.
    Returns the matched run ID, or None if not found.
    """
    try:
        from langsmith import Client
    except ImportError:
        warn("langsmith not installed -- skipping LangSmith step")
        warn("pip install langsmith  to enable this step")
        return None

    langsmith_key = _load_langsmith_key(project_root)
    if not langsmith_key:
        warn("No LANGCHAIN_API_KEY found -- skipping LangSmith step")
        return None

    project_name = os.environ.get("LANGCHAIN_PROJECT", "ChadAi")
    info(f"Querying LangSmith project '{project_name}' for tag: PROBE:{trace_tag}")
    info(f"Waiting {ingestion_wait:.0f}s for ingestion lag...")
    time.sleep(ingestion_wait)

    try:
        client = Client()
        runs = list(client.list_runs(
            project_name=project_name,
            run_type="llm",
            limit=10,
        ))
    except Exception as e:
        warn(f"LangSmith query failed: {e}")
        return None

    if not runs:
        warn(f"No LLM runs found in project '{project_name}'")
        return None

    matched = None
    for r in runs:
        msgs = str((r.inputs or {}).get("messages", ""))
        if f"PROBE:{trace_tag}" in msgs:
            matched = r
            break

    if not matched:
        warn(f"No run found containing PROBE:{trace_tag} in the last {len(runs)} runs")
        warn("LangSmith ingestion may be slow -- re-run perfect_trace.py manually:")
        warn(f"  python scripts/perfect_trace.py --match PROBE:{trace_tag}")
        return None

    ok(f"Found matching LangSmith run: {matched.name} | id={matched.id}")
    info(f"Run perfect_trace.py for full diagnostic:")
    info(f"  python scripts/perfect_trace.py {matched.id}")
    info(f"  (run ID also saved to probe JSON for cross-validation)")

    # Check if reasoning is captured
    gens = (matched.outputs or {}).get("generations", [])
    if gens and isinstance(gens, list) and gens[0]:
        gen = gens[0][0] if isinstance(gens[0], list) else gens[0]
        msg = gen.get("message", {}) if isinstance(gen, dict) else {}
        kw = msg.get("kwargs", {}) if isinstance(msg, dict) else {}
        reasoning = kw.get("additional_kwargs", {}).get("reasoning", "")
        if reasoning:
            ok(f"Reasoning IS captured in LangSmith ({len(reasoning)} chars)")
        else:
            warn("Reasoning NOT in LangSmith output -- lc_wrapper fix may not be deployed yet")

    return matched.id


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="trace_probe.py -- autonomous pipeline probe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single turn
  python scripts/trace_probe.py --prompt "What time is it?"

  # Multi-turn plan (keeps WebSocket open, shares conversation history)
  python scripts/trace_probe.py --plan \\
      "Draft a short email to my manager about project status" \\
      "Save that as a draft" \\
      "Remind me to send it tomorrow at 9am"

  # Planner→Agent (interactive approval between planner and agent nodes)
  python scripts/trace_probe.py --planner "Plan how to back up my documents folder"
  python scripts/trace_probe.py --planner "Plan a DB schema migration" \\
      --agent-prompt "Execute the migration plan step by step"
""")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prompt", "-p",
                       default="What time is it right now?",
                       help="Single prompt to send to Chad")
    group.add_argument("--plan", nargs="+", metavar="PROMPT",
                       help="Multi-turn plan: list of prompts executed in sequence "
                            "over a single shared conversation session")
    group.add_argument("--planner", metavar="PROMPT",
                       help="Planner→Agent mode: send a planning prompt, pause for "
                            "interactive approval, then send --agent-prompt as the "
                            "agent execution turn (same conv_id, new WS connection)")
    parser.add_argument("--agent-prompt", default="Execute the plan above.",
                        help="Prompt sent as the agent node turn after the user approves "
                             "the plan (default: 'Execute the plan above.')")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Planner→Agent mode: skip interactive approval and run the "
                             "agent node automatically after the planner responds")
    parser.add_argument("--stop-after", type=int, default=8, metavar="N",
                        help="Stop after step N (default: run all steps)")
    parser.add_argument("--no-langsmith", action="store_true",
                        help="Skip LangSmith step")
    parser.add_argument("--project-root", default=None,
                        help="Path to project root (auto-detected if not set)")
    args = parser.parse_args()

    # Detect project root
    if args.project_root:
        project_root = Path(args.project_root).resolve()
    else:
        here = Path(__file__).resolve().parent
        for candidate in [here.parent, here.parent.parent, here.parent.parent.parent]:
            if (candidate / "requirements.txt").exists() or (candidate / "pyproject.toml").exists():
                project_root = candidate
                break
        else:
            project_root = here.parent

    info(f"Project root: {project_root}")

    is_plan = bool(args.plan)
    is_planner_agent = bool(args.planner)
    prompts = args.plan if is_plan else ([args.planner] if is_planner_agent else [args.prompt])

    # Generate a unique tag per turn + a shared conv_id for the session
    turn_tags = [uuid.uuid4().hex[:8].upper() for _ in prompts]
    if is_planner_agent:
        turn_tags.append(uuid.uuid4().hex[:8].upper())  # reserve tag for agent turn
    plan_id = uuid.uuid4().hex[:8].upper()
    conv_id = f"probe_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    if is_planner_agent:
        info(f"Mode:        PLANNER → AGENT (interactive approval)")
        info(f"Plan ID:     PLAN:{plan_id}")
        info(f"Conv ID:     {conv_id}")
        info(f"Planner:     [PROBE:{turn_tags[0]}] {args.planner[:80]}")
        info(f"Agent:       [PROBE:{turn_tags[1]}] {args.agent_prompt[:80]}")
    elif is_plan:
        info(f"Mode:        MULTI-TURN PLAN ({len(prompts)} turns)")
        info(f"Plan ID:     PLAN:{plan_id}")
        info(f"Conv ID:     {conv_id}")
        for i, (p, tag) in enumerate(zip(prompts, turn_tags), 1):
            info(f"Turn {i}:      [PROBE:{tag}] {p[:80]}")
    else:
        info(f"Mode:        Single turn")
        info(f"Trace tag:   PROBE:{turn_tags[0]}")
        info(f"Prompt:      {prompts[0]}")

    # Steps 1-3
    auth_token = load_auth_token(project_root)
    if args.stop_after < 2:
        print("\nStopped after step 1 (--stop-after 1)"); return

    check_server_health()
    if args.stop_after < 3:
        print("\nStopped after step 2 (--stop-after 2)"); return

    local_cfg = read_local_model_config(project_root)
    check_lm_studio(local_cfg)
    if args.stop_after < 4:
        print("\nStopped after step 3 (--stop-after 3)"); return

    # Steps 4-6: run WebSocket session (single, multi-turn, or planner→agent)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if is_planner_agent:
        # --- Planner node ---
        step("4-6 [PLANNER]", "Planner node — send planning prompt")
        planner_pairs = [(args.planner, turn_tags[0], "planning")]
        planner_traces = asyncio.run(run_ws_session(auth_token, planner_pairs, conv_id))
        planner_trace = planner_traces[0]

        if args.stop_after < 7:
            print(f"\nStopped after planner node (--stop-after {args.stop_after})"); return

        # --- Interactive approval ---
        plan_text = planner_trace.get("final_response", "")
        print(f"\n{'='*60}")
        print(f"  PLANNER RESPONSE:")
        print(f"{'='*60}")
        print(plan_text or "(no response captured)")
        print(f"{'='*60}\n")

        if args.auto_approve:
            ok("--auto-approve set — skipping interactive approval, running agent node...")
            answer = "y"
        else:
            try:
                answer = input("  Approve plan and run agent node? [y/N/q] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

        if answer not in ("y", "yes"):
            warn("Plan not approved — skipping agent node.")
            turn_traces = [planner_trace]
        else:
            ok("Plan approved — running agent node...")
            # --- Agent node (new WS connection, same conv_id) ---
            step("4-6 [AGENT]", "Agent node — execute plan")
            agent_pairs = [(args.agent_prompt, turn_tags[1], "agent")]
            agent_traces = asyncio.run(run_ws_session(auth_token, agent_pairs, conv_id))
            turn_traces = [planner_trace, agent_traces[0]]
    else:
        turn_pairs = list(zip(prompts, turn_tags))
        turn_traces = asyncio.run(run_ws_session(auth_token, turn_pairs, conv_id))

    if args.stop_after < 7:
        print(f"\nStopped after step 6 (--stop-after {args.stop_after})"); return

    # Step 7: Save traces
    step(7, "Save local trace(s)")
    data_dir = project_root / "tests" / "data"
    saved_paths = []

    if is_planner_agent or is_plan:
        # Save individual turn traces
        for t in turn_traces:
            out_path = data_dir / f"probe_{t['trace_tag']}.json"
            save_trace(t, out_path)
            saved_paths.append(out_path)
        # Save combined plan trace
        all_prompts = ([args.planner, args.agent_prompt] if is_planner_agent
                       else prompts)
        plan_path = data_dir / f"plan_{plan_id}.json"
        save_plan_trace(turn_traces, plan_id, all_prompts[:len(turn_traces)], plan_path)
        saved_paths.append(plan_path)
    else:
        out_path = data_dir / f"probe_{turn_tags[0]}.json"
        save_trace(turn_traces[0], out_path)
        saved_paths.append(out_path)

    if args.stop_after < 8:
        print(f"\nStopped after step 7 (--stop-after 7)"); return

    # Step 8: Pull LangSmith traces
    if not args.no_langsmith:
        step(8, f"Pull LangSmith trace(s) ({len(turn_traces)} turn(s))")
        run_ids = []
        actual_tags = [t["trace_tag"] for t in turn_traces]
        for i, (t, tag) in enumerate(zip(turn_traces, actual_tags), 1):
            node_label = (["planner", "agent"][i - 1] if is_planner_agent and i <= 2
                          else f"turn {i}")
            info(f"\nQuerying LangSmith for {node_label} | PROBE:{tag}")
            wait = 8.0 if i == 1 else 3.0  # first turn waits for ingestion; subsequent turns are already ingested
            run_id = pull_langsmith_trace(tag, project_root, ingestion_wait=wait)
            if run_id:
                run_ids.append((i, tag, run_id))
                # Patch the probe JSON with the confirmed LangSmith run ID for cross-validation
                probe_path = data_dir / f"probe_{tag}.json"
                if probe_path.exists():
                    try:
                        probe_data = json.loads(probe_path.read_text(encoding="utf-8"))
                        probe_data["langsmith_run_id"] = run_id
                        probe_path.write_text(json.dumps(probe_data, indent=2, default=str), encoding="utf-8")
                        ok(f"Patched probe JSON with langsmith_run_id: {run_id}")
                    except Exception as e:
                        warn(f"Could not patch probe JSON with run ID: {e}")

        if run_ids:
            print(f"\n  {ANSI['ok']}LangSmith run IDs:{ANSI['reset']}")
            for turn_n, tag, run_id in run_ids:
                if is_planner_agent:
                    label = ["planner", "agent"][turn_n - 1] if turn_n <= 2 else f"turn{turn_n}"
                    suffix = f"--out-suffix _{label}"
                elif is_plan:
                    label, suffix = f"turn{turn_n}", f"--out-suffix _turn{turn_n}"
                else:
                    label, suffix = f"turn{turn_n}", ""
                print(f"    [{label}] [{tag}]: python scripts/perfect_trace.py {run_id} {suffix}")

    # Final summary
    total_turns = len(turn_traces)
    total_tools = sum(len(t.get("tool_calls", [])) for t in turn_traces)
    total_reasoning = sum(t.get("reasoning_chars", 0) for t in turn_traces)
    total_duration = sum(t.get("duration_s", 0) for t in turn_traces)
    overall_status = "success" if all(t.get("status") == "success" for t in turn_traces) else "failure"

    print(f"\n{ANSI['bold']}{ANSI['ok']}{'=' * 60}{ANSI['reset']}")
    if is_planner_agent:
        node_labels = ["planner", "agent"]
        print(f"{ANSI['bold']}  PLANNER→AGENT PROBE COMPLETE{ANSI['reset']}")
        print(f"  Plan ID   : PLAN:{plan_id}")
        print(f"  Nodes run : {total_turns}")
        for i, t in enumerate(turn_traces, 1):
            label = node_labels[i - 1] if i <= 2 else f"turn{i}"
            print(f"    [{label}]: [{t['trace_tag']}] {t['status'].upper()} "
                  f"| {t.get('duration_s')}s | {len(t.get('tool_calls',[]))} tools "
                  f"| {t.get('reasoning_chars',0)} reasoning chars")
    elif is_plan:
        print(f"{ANSI['bold']}  PLAN PROBE COMPLETE{ANSI['reset']}")
        print(f"  Plan ID   : PLAN:{plan_id}")
        print(f"  Turns     : {total_turns}")
        for i, t in enumerate(turn_traces, 1):
            print(f"    Turn {i}  : [{t['trace_tag']}] {t['status'].upper()} "
                  f"| {t.get('duration_s')}s | {len(t.get('tool_calls',[]))} tools "
                  f"| {t.get('reasoning_chars',0)} reasoning chars")
    else:
        print(f"{ANSI['bold']}  PROBE COMPLETE{ANSI['reset']}")
        print(f"  Status    : {overall_status.upper()}")
        print(f"  Trace tag : PROBE:{turn_tags[0]}")
    print(f"  Duration  : {total_duration:.1f}s total")
    print(f"  Reasoning : {total_reasoning} chars captured from stream")
    print(f"  Tools     : {total_tools} total calls")
    print(f"  Saved to  : {', '.join(str(p) for p in saved_paths)}")
    if is_planner_agent or is_plan:
        node_or_turn = "node" if is_planner_agent else "turn"
        print(f"\n  Run analyze_trace.py for each {node_or_turn} to get health cards:")
        for i, t in enumerate(turn_traces, 1):
            label = (["planner", "agent"][i - 1] if is_planner_agent and i <= 2
                     else f"turn{i}")
            print(f"    python scripts/perfect_trace.py <run_id_{label}>  (run ID printed above)")
            print(f"    python scripts/analyze_trace.py --out-suffix _{label}")
    print(f"{ANSI['bold']}{ANSI['ok']}{'=' * 60}{ANSI['reset']}\n")


if __name__ == "__main__":
    main()
