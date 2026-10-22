"""
benchmark_pipeline.py — Chad request pipeline benchmark.

Sends real WebSocket turns to the running Brain server, timestamps every
observable event, and produces a structured per-phase timing report.

Measures (from WebSocket event timestamps — no code instrumentation needed):
  - Pre-model setup  : user_input sent → first ai_thought/reasoning or first token
  - TTFT             : user_input sent → first streaming ai_response fragment
  - Model generation : first token → last token (excluding tool time)
  - Tool execution   : per-tool (tool_start → tool_end), sum for total
  - Total turn time  : user_input sent → final non-fragment ai_response

Usage:
    # Server must be running first:
    #   $env:PYTHONPATH="."; $env:CHAD_DAEMONS_ENABLED="false"; .venv\\Scripts\\python.exe src\\remote\\server.py

    # Baseline measurement (3 iterations):
    python scripts/benchmark_pipeline.py

    # Custom prompt / iteration count:
    python scripts/benchmark_pipeline.py --prompt "summarize my notes" --iterations 5

    # Apply asyncio.gather optimisation then retest and compare:
    python scripts/benchmark_pipeline.py --apply-fix

    # Compare two saved benchmark files:
    python scripts/benchmark_pipeline.py --compare tests/data/benchmark_BEFORE_*.json

    # Revert the applied fix:
    python scripts/benchmark_pipeline.py --revert

Output: tests/data/benchmark_{BEFORE|AFTER}_YYYYMMDD_HHMMSS.json
"""

import asyncio
import json
import os
import re
import sys
import time
import uuid
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

# ── Windows compat ────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "tests" / "data"

# ── Network constants ─────────────────────────────────────────────────────────
SERVER_URL          = "ws://localhost:8767/ws"
HEALTH_URL          = "http://localhost:8767/health"
LM_URL              = "http://localhost:1234/v1"
FIRST_EVENT_TIMEOUT = 120   # seconds — long enough for cold LLM load
RESPONSE_TIMEOUT    = 300   # seconds for complete AI response

# ── ANSI colours ──────────────────────────────────────────────────────────────
A = {
    "ok":    "\033[92m",
    "warn":  "\033[93m",
    "err":   "\033[91m",
    "info":  "\033[96m",
    "dim":   "\033[2m",
    "bold":  "\033[1m",
    "reset": "\033[0m",
}


# ── Print helpers ──────────────────────────────────────────────────────────────

def _ts() -> str:
    return time.strftime("%H:%M:%S")

def ok(msg: str)   -> None: print(f"  {A['ok']}[{_ts()}] OK  {A['reset']} {msg}")
def warn(msg: str) -> None: print(f"  {A['warn']}[{_ts()}] WARN{A['reset']} {msg}")
def info(msg: str) -> None: print(f"  {A['info']}[{_ts()}] INFO{A['reset']} {msg}")
def err(msg: str)  -> None: print(f"  {A['err']}[{_ts()}] ERR {A['reset']} {msg}")

def section(title: str) -> None:
    bar = "═" * 62
    print(f"\n{A['bold']}{A['info']}{bar}{A['reset']}")
    print(f"{A['bold']}{A['info']}  {title}{A['reset']}")
    print(f"{A['bold']}{A['info']}{bar}{A['reset']}")

def die(msg: str, hint: str = "") -> None:
    err(f"FATAL: {msg}")
    if hint:
        warn(f"Fix:   {hint}")
    sys.exit(1)


# ── Auth token (mirrors trace_probe.py) ───────────────────────────────────────

def load_auth_token() -> str:
    for key in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN"):
        v = os.environ.get(key, "").strip()
        if v:
            ok(f"Token from env {key}")
            return v

    candidate = ROOT
    for _ in range(6):
        dotenv = candidate / ".env"
        if dotenv.exists():
            with open(dotenv, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN") and v:
                        ok(f"Token from .env ({k})")
                        return v
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    candidate = ROOT
    for _ in range(6):
        secrets = candidate / "config" / "secrets.yaml"
        if secrets.exists():
            with open(secrets, encoding="utf-8") as f:
                for line in f:
                    for key in ("chad_remote_token", "chad_server_token"):
                        if key in line.lower() and ":" in line:
                            v = line.split(":", 1)[1].strip().strip('"').strip("'")
                            if v and v.lower() != "null":
                                ok(f"Token from secrets.yaml ({key})")
                                return v
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    die(
        "No auth token found in env, .env, or config/secrets.yaml",
        "Set CHAD_REMOTE_TOKEN=<your_token> in your .env file",
    )


# ── Health check ──────────────────────────────────────────────────────────────

def check_server_health() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5.0) as resp:
            return resp.status == 200
    except Exception:
        return False


# ── WS protocol helper ────────────────────────────────────────────────────────

def _make_msg(msg_type: str, payload: dict = None, auth_token: str = None) -> str:
    return json.dumps({
        "id": str(uuid.uuid4())[:8],
        "type": msg_type,
        "payload": payload or {},
        "auth_token": auth_token,
        "timestamp": time.time(),
    })


# ── Single-turn timing measurement ────────────────────────────────────────────

async def measure_turn(ws, prompt: str, conv_id: str) -> Dict:
    """
    Send one prompt over an authenticated WS and record per-phase timings.

    Phases visible from the event stream:
      - pre_model_ms  : submit → first ai_thought[reasoning] or first fragment
      - ttft_ms       : submit → first ai_response fragment
      - generation_ms : first fragment → final response (minus tool time)
      - tool_total_ms : sum of (tool_start → tool_end) durations
      - total_ms      : submit → final non-fragment ai_response
    """
    timing: Dict[str, Any] = {
        "conv_id": conv_id,
        "prompt": prompt,
        "t_submit": None,
        "t_first_event": None,
        "t_first_thinking": None,
        "t_first_fragment": None,
        "t_last_fragment": None,
        "t_final": None,
        "tools": [],
        "events": [],
        "final_response": None,
        "status": "pending",
        "error": None,
        # derived below
        "pre_model_ms": None,
        "ttft_ms": None,
        "generation_ms": None,
        "tool_total_ms": None,
        "total_ms": None,
    }

    timing["t_submit"] = time.time()
    await ws.send(_make_msg("user_input", {
        "text": prompt,
        "initiator": "user",
        "conversation_id": conv_id,
        "category": "agent",
    }))
    info(f"→ user_input sent  conv_id={conv_id[:8]}")

    pending_tools: Dict[str, float] = {}  # tool_name → t_start
    deadline = time.time() + RESPONSE_TIMEOUT
    first_event_received = False

    while time.time() < deadline:
        remaining = deadline - time.time()
        wait = min(FIRST_EVENT_TIMEOUT if not first_event_received else 5.0, remaining)

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=wait)
        except asyncio.TimeoutError:
            if not first_event_received:
                timing["status"] = "timeout_first_event"
                timing["error"] = f"No event in {FIRST_EVENT_TIMEOUT}s"
                err("Timeout waiting for first event — is LM Studio running?")
                return timing
            continue  # still within response timeout, keep waiting

        t_now = time.time()

        try:
            evt = json.loads(raw)
        except Exception:
            continue

        etype    = evt.get("type", "")
        epayload = evt.get("payload", {})

        if not first_event_received:
            timing["t_first_event"] = t_now
            first_event_received = True

        timing["events"].append({
            "t": t_now,
            "type": etype,
            "state": epayload.get("state", ""),
            "fragment": epayload.get("fragment"),
        })

        # ── reasoning / thinking ──────────────────────────────────────────────
        if etype == "ai_thought":
            state = epayload.get("state", "")

            if state in ("reasoning", "thinking") and timing["t_first_thinking"] is None:
                timing["t_first_thinking"] = t_now
                info(f"  ← first thinking  +{(t_now - timing['t_submit'])*1000:.0f}ms")

            elif state == "tool_start":
                tool_name = epayload.get("name", f"tool_{len(timing['tools'])}")
                pending_tools[tool_name] = t_now
                info(f"  ← tool_start: {tool_name}")

            elif state == "tool_end":
                tool_name = epayload.get("name", "")
                t_start = pending_tools.pop(tool_name, t_now)
                dur_ms = (t_now - t_start) * 1000
                timing["tools"].append({
                    "name": tool_name,
                    "t_start": t_start,
                    "t_end": t_now,
                    "duration_ms": dur_ms,
                })
                info(f"  ← tool_end:   {tool_name}  ({dur_ms:.0f}ms)")

        # ── streaming response ────────────────────────────────────────────────
        elif etype == "ai_response":
            is_fragment = epayload.get("fragment", False)
            if is_fragment:
                if timing["t_first_fragment"] is None:
                    timing["t_first_fragment"] = t_now
                    info(f"  ← first token   +{(t_now - timing['t_submit'])*1000:.0f}ms (TTFT)")
                timing["t_last_fragment"] = t_now
            else:
                timing["t_final"] = t_now
                timing["final_response"] = epayload.get("text", "")
                timing["status"] = "success"
                ok(f"← final response  +{(t_now - timing['t_submit'])*1000:.0f}ms  "
                   f"({len(timing['final_response'])} chars)")
                break

        # ── errors ───────────────────────────────────────────────────────────
        elif etype in ("error", "system_error"):
            timing["status"] = "error"
            timing["error"] = epayload.get("error", str(epayload))
            err(f"Server error: {timing['error']}")
            return timing

    if timing["status"] == "pending":
        timing["status"] = "timeout"
        timing["error"] = f"No final response within {RESPONSE_TIMEOUT}s"

    # ── derive phase durations ────────────────────────────────────────────────
    t0 = timing["t_submit"]
    if timing["t_final"] and t0:
        timing["total_ms"] = (timing["t_final"] - t0) * 1000

        first_model_signal = timing["t_first_thinking"] or timing["t_first_fragment"]
        if first_model_signal:
            timing["pre_model_ms"] = (first_model_signal - t0) * 1000

        if timing["t_first_fragment"]:
            timing["ttft_ms"] = (timing["t_first_fragment"] - t0) * 1000

        timing["tool_total_ms"] = sum(t["duration_ms"] for t in timing["tools"])

        pre    = timing.get("pre_model_ms") or 0
        tools_ = timing["tool_total_ms"]
        timing["generation_ms"] = max(0.0, timing["total_ms"] - pre - tools_)

    return timing


# ── WS session ────────────────────────────────────────────────────────────────

async def run_benchmark_session(auth_token: str, turns: List[Tuple[str, str]]) -> List[Dict]:
    """
    Connect once, authenticate, then run each (prompt, conv_id) pair sequentially.
    Each turn gets its own conv_id so Supabase history doesn't accumulate across runs.
    """
    try:
        import websockets
    except ImportError:
        die("websockets not installed", "pip install websockets")

    headers = {"Origin": "http://localhost:5173"}
    try:
        ws_ctx = websockets.connect(
            SERVER_URL, additional_headers=headers,
            ping_interval=None, open_timeout=20,
        )
    except TypeError:
        ws_ctx = websockets.connect(
            SERVER_URL, extra_headers=headers,
            ping_interval=None, open_timeout=20,
        )

    results = []
    async with ws_ctx as ws:
        # Authenticate
        await ws.send(_make_msg("auth", auth_token=auth_token))
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        resp = json.loads(raw)
        if resp.get("payload", {}).get("status") != "success":
            die(f"Auth failed: {resp}")
        ok("Authenticated")

        for i, (prompt, conv_id) in enumerate(turns):
            section(f"Iteration {i + 1} / {len(turns)}")
            timing = await measure_turn(ws, prompt, conv_id)
            results.append(timing)

            if timing["status"] == "success":
                total = timing.get("total_ms") or 0
                ttft  = timing.get("ttft_ms") or 0
                pre   = timing.get("pre_model_ms") or 0
                ok(f"total={total:.0f}ms | pre_model={pre:.0f}ms | TTFT={ttft:.0f}ms")
            else:
                warn(f"status={timing['status']}: {timing.get('error', '')}")

    return results


# ── Statistics ─────────────────────────────────────────────────────────────────

def _stats(values: List[float]) -> Dict:
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "n": 0}
    n    = len(values)
    mean = sum(values) / n
    var  = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    return {"mean": mean, "min": min(values), "max": max(values), "std": var ** 0.5, "n": n}


def aggregate(results: List[Dict]) -> Dict:
    successful = [r for r in results if r.get("status") == "success"]
    if not successful:
        return {"error": "No successful turns", "n_total": len(results), "n_success": 0}

    def vals(field: str) -> List[float]:
        return [r[field] for r in successful if r.get(field) is not None]

    agg: Dict[str, Any] = {
        "n_total":        len(results),
        "n_success":      len(successful),
        "total_ms":       _stats(vals("total_ms")),
        "pre_model_ms":   _stats(vals("pre_model_ms")),
        "ttft_ms":        _stats(vals("ttft_ms")),
        "generation_ms":  _stats(vals("generation_ms")),
        "tool_total_ms":  _stats(vals("tool_total_ms")),
    }

    all_tools: Dict[str, List[float]] = {}
    for r in successful:
        for t in r.get("tools", []):
            all_tools.setdefault(t["name"], []).append(t["duration_ms"])
    agg["tool_breakdown"] = {name: _stats(durs) for name, durs in all_tools.items()}

    return agg


# ── Human-readable report ──────────────────────────────────────────────────────

def _bar(value: float, max_val: float, width: int = 30) -> str:
    n = int((value / max_val) * width) if max_val > 0 else 0
    return A["info"] + "█" * n + A["reset"]


def print_report(agg: Dict, title: str = "Benchmark Report") -> None:
    section(title)

    if "error" in agg:
        err(agg["error"])
        return

    print(f"\n  Successful runs : {agg['n_success']} / {agg['n_total']}")
    print()

    rows = [
        ("Total turn time",    agg["total_ms"]),
        ("  Pre-model setup",  agg["pre_model_ms"]),
        ("  Model generation", agg["generation_ms"]),
        ("  Tool execution",   agg["tool_total_ms"]),
        ("TTFT (user-facing)", agg["ttft_ms"]),
    ]

    max_mean = max((r[1]["mean"] for r in rows), default=1) or 1

    header = f"  {'Phase':<24} {'Mean':>9} {'Min':>9} {'Max':>9} {'±':>8}   Chart"
    print(header)
    print("  " + "─" * (len(header) + 10))

    for label, s in rows:
        bar = _bar(s["mean"], max_mean)
        print(f"  {label:<24} {s['mean']:>8.0f}ms {s['min']:>8.0f}ms "
              f"{s['max']:>8.0f}ms {s['std']:>7.0f}ms   {bar}")

    # Tool breakdown
    if agg["tool_breakdown"]:
        print("\n  Tool call breakdown:")
        for name, s in sorted(agg["tool_breakdown"].items(),
                               key=lambda x: x[1]["mean"], reverse=True):
            print(f"    {name:<32} mean={s['mean']:>7.0f}ms  n={s['n']}")

    # Bottleneck analysis
    print(f"\n  {A['bold']}Bottleneck Analysis:{A['reset']}")
    total_mean = agg["total_ms"]["mean"]
    phases = [
        ("Pre-model setup",  agg["pre_model_ms"]["mean"]),
        ("Model generation", agg["generation_ms"]["mean"]),
        ("Tool execution",   agg["tool_total_ms"]["mean"]),
    ]
    phases.sort(key=lambda x: x[1], reverse=True)
    for rank, (name, ms) in enumerate(phases):
        pct    = (ms / total_mean * 100) if total_mean > 0 else 0
        marker = f"  {A['warn']}<-- LARGEST GAP{A['reset']}" if rank == 0 else ""
        print(f"    {rank+1}. {name:<22} {ms:>8.0f}ms  ({pct:.0f}%){marker}")

    # Recommendation
    top_phase = phases[0][0] if phases else ""
    print(f"\n  {A['bold']}Recommendation:{A['reset']}")
    if top_phase == "Pre-model setup":
        pre_ms = agg["pre_model_ms"]["mean"]
        print(f"  Pre-model setup is {pre_ms:.0f}ms. The bottleneck within this phase is")
        print(f"  tool selection: a blocking HTTP call to LM Studio (up to 45s timeout)")
        print(f"  running SEQUENTIALLY after history fetch in turn_coordinator.py:760.")
        print()
        print(f"  Fastest safe fix: parallelise history fetch + tool selection with")
        print(f"  asyncio.gather — they have no data dependency. Expected saving:")
        print(f"  ~200–500ms (the history fetch latency, which is currently wasted")
        print(f"  while the event loop blocks on the tool-selection thread).")
        print()
        print(f"  To apply: python scripts/benchmark_pipeline.py --apply-fix")
    elif top_phase == "Model generation":
        print("  Model generation dominates — LLM inference time.")
        print("  Consider: faster model tier, lower thinking budget, or response caching.")
    elif top_phase == "Tool execution":
        print("  Tool calls dominate. Consider parallelising independent tool calls")
        print("  within the LangGraph node, or reducing network latency to tool targets.")


def print_comparison(before: Dict, after: Dict, title: str = "Before vs After") -> None:
    section(title)

    fields = [
        ("total_ms",      "Total turn time"),
        ("pre_model_ms",  "Pre-model setup"),
        ("ttft_ms",       "TTFT"),
        ("generation_ms", "Generation"),
        ("tool_total_ms", "Tool execution"),
    ]

    print(f"\n  {'Phase':<24} {'Before':>10} {'After':>10} {'Delta':>12} {'Change':>8}")
    print("  " + "─" * 70)

    for field, label in fields:
        b = before.get(field, {}).get("mean", 0) or 0
        a = after.get(field,  {}).get("mean", 0) or 0
        if b == 0 and a == 0:
            continue
        delta = a - b
        pct   = (delta / b * 100) if b > 0 else 0
        sign  = "+" if delta >= 0 else ""
        color = A["ok"] if delta < -10 else (A["err"] if delta > 50 else A["warn"])
        print(f"  {label:<24} {b:>9.0f}ms {a:>9.0f}ms "
              f"{color}{sign}{delta:>+9.0f}ms ({pct:>+.1f}%){A['reset']}")


# ── asyncio.gather fix ────────────────────────────────────────────────────────

_GATHER_MARKER = "# [BENCHMARK-FIX] asyncio.gather history+tools"
_TC_PATH = ROOT / "src" / "core" / "orchestration" / "turn_coordinator.py"
_TC_BACKUP = _TC_PATH.with_suffix(".py.benchmark_bak")


def check_fix_applied() -> bool:
    return _GATHER_MARKER in _TC_PATH.read_text(encoding="utf-8")


def apply_gather_fix() -> bool:
    """
    Parallel-gather history fetch + tool selection in turn_coordinator.py.

    These two steps are currently sequential (history fetch at line ~276,
    tool selection at line ~293) but have no data dependency between them.
    Running them concurrently saves the history-fetch round-trip (~200–500ms
    from Supabase) from blocking behind the tool-selection LLM call.
    """
    content = _TC_PATH.read_text(encoding="utf-8")

    if _GATHER_MARKER in content:
        warn("Fix already applied — skipping")
        return True

    # Locate the sequential history+tools block.
    # Pattern anchors on the comment header and the tool-selection call.
    pattern = re.compile(
        r"([ \t]+# 2\. Fetch History for Systems Prep.*?)"   # start marker
        r"([ \t]+# 3\. Get Optimized Tools\n"
        r"[ \t]+tools = await self\._get_optimized_tools\(prompt, category=category\))",
        re.DOTALL,
    )
    m = pattern.search(content)
    if not m:
        err("Could not locate the history+tool_selection block in turn_coordinator.py")
        err("The file may have been modified. Apply the fix manually — see SKILL.md.")
        return False

    # Replacement: wrap both in a nested async fn + asyncio.gather
    replacement = (
        f"        {_GATHER_MARKER}\n"
        r"        # 2+3. History fetch and tool selection run concurrently — no data dep."
        "\n"
        r"        async def _fetch_history_async():"
        "\n"
        r"            _hist = []"
        "\n"
        r"            if session_id and session_id != 'default':"
        "\n"
        r"                from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository"
        "\n"
        r"                _repo = ChatHistoryRepository()"
        "\n"
        r"                try:"
        "\n"
        r"                    db_messages = await _repo.get_messages(session_id, include_archived=False)"
        "\n"
        r"                    if db_messages:"
        "\n"
        r"                        for msg in db_messages:"
        "\n"
        r"                            role = msg.get('role')"
        "\n"
        r"                            db_content = msg.get('content', '')"
        "\n"
        r"                            m = {'role': role, 'id': msg.get('id')}"
        "\n"
        r"                            if isinstance(db_content, list):"
        "\n"
        r"                                m['content_blocks'] = db_content"
        "\n"
        r"                                m['content'] = next((b.get('text') for b in db_content if b.get('type') == 'text'), '[Attached Content]')"
        "\n"
        r"                            else:"
        "\n"
        r"                                m['content'] = str(db_content)"
        "\n"
        r"                            _hist.append(m)"
        "\n"
        r"                        logger.info(f'[ TURN ] Loaded {len(_hist)} un-archived historical messages for session {session_id}')"
        "\n"
        r"                except Exception as e:"
        "\n"
        r"                    logger.warning(f'[ TURN ] Failed to retrieve history for prompt engineering: {e}')"
        "\n"
        r"            return _hist"
        "\n\n"
        r"        history, tools = await asyncio.gather("
        "\n"
        r"            _fetch_history_async(),"
        "\n"
        r"            self._get_optimized_tools(prompt, category=category),"
        "\n"
        r"        )"
    )

    _TC_BACKUP.write_text(content, encoding="utf-8")
    ok(f"Backup saved: {_TC_BACKUP.name}")

    new_content = content[:m.start()] + replacement + content[m.end():]
    _TC_PATH.write_text(new_content, encoding="utf-8")
    ok(f"Fix applied to {_TC_PATH.name}")
    return True


def revert_gather_fix() -> bool:
    if not _TC_BACKUP.exists():
        warn("No backup found — fix was not applied by this script")
        return False
    original = _TC_BACKUP.read_text(encoding="utf-8")
    _TC_PATH.write_text(original, encoding="utf-8")
    _TC_BACKUP.unlink()
    ok(f"Reverted {_TC_PATH.name} from backup")
    return True


# ── Save / load results ────────────────────────────────────────────────────────

def save_results(agg: Dict, raw: List[Dict], label: str = "") -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp  = time.strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    path   = OUTPUT_DIR / f"benchmark{suffix}_{stamp}.json"
    data   = {"timestamp": stamp, "label": label, "aggregated": agg, "raw": raw}
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    ok(f"Saved: {path}")
    return path


def load_results(path: str) -> Dict:
    p = Path(path)
    if not p.exists():
        die(f"File not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("aggregated", {})


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chad pipeline benchmark — measure real WS turn latency."
    )
    parser.add_argument(
        "--iterations", "-n", type=int, default=3,
        help="Number of benchmark iterations (default: 3)",
    )
    parser.add_argument(
        "--prompt", default="What is the capital of France? Answer in one sentence.",
        help="Prompt to benchmark (pick something with a deterministic short answer)",
    )
    parser.add_argument(
        "--apply-fix", action="store_true",
        help="Apply asyncio.gather optimisation, retest, and compare before/after",
    )
    parser.add_argument(
        "--revert", action="store_true",
        help="Revert the asyncio.gather fix applied by --apply-fix",
    )
    parser.add_argument(
        "--compare", metavar="FILE",
        help="Compare current run against a saved benchmark JSON (e.g. BEFORE file)",
    )
    args = parser.parse_args()

    section("Chad Pipeline Benchmark")
    print(f"  Iterations : {args.iterations}")
    print(f"  Prompt     : {args.prompt[:80]}")

    # Revert-only mode
    if args.revert:
        revert_gather_fix()
        return

    auth_token = load_auth_token()

    # Verify server
    info("Checking server health...")
    if not check_server_health():
        die(
            "Server is not running",
            "$env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
            ".venv\\Scripts\\python.exe src\\remote\\server.py",
        )
    ok("Server is healthy")

    # ── Baseline ──────────────────────────────────────────────────────────────
    turns = [(args.prompt, str(uuid.uuid4())) for _ in range(args.iterations)]
    raw_before = asyncio.run(run_benchmark_session(auth_token, turns))
    agg_before = aggregate(raw_before)
    print_report(agg_before, "Baseline Results")
    before_path = save_results(agg_before, raw_before, "BEFORE")

    # ── Optional external comparison ──────────────────────────────────────────
    if args.compare:
        ref_agg = load_results(args.compare)
        print_comparison(ref_agg, agg_before, f"Comparison: {args.compare} vs this run")

    # ── Apply fix + retest ────────────────────────────────────────────────────
    if args.apply_fix:
        section("Applying asyncio.gather Optimisation")

        already = check_fix_applied()
        if not already:
            if not apply_gather_fix():
                die("Fix application failed — see errors above")

        print()
        warn("You must restart the server for the code change to take effect.")
        warn("Run in another terminal:")
        warn("  $env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
             ".venv\\Scripts\\python.exe src\\remote\\server.py")
        input("\n  Press Enter once the server has restarted... ")

        turns_after = [(args.prompt, str(uuid.uuid4())) for _ in range(args.iterations)]
        raw_after   = asyncio.run(run_benchmark_session(auth_token, turns_after))
        agg_after   = aggregate(raw_after)
        print_report(agg_after, "Post-Fix Results")
        save_results(agg_after, raw_after, "AFTER")
        print_comparison(agg_before, agg_after)

        # Quality check — make sure responses still look valid
        section("Quality Check (response content)")
        for i, (b, a) in enumerate(zip(raw_before, raw_after)):
            b_resp = (b.get("final_response") or "")[:200]
            a_resp = (a.get("final_response") or "")[:200]
            if b_resp or a_resp:
                print(f"  Run {i+1} BEFORE: {b_resp!r}")
                print(f"  Run {i+1} AFTER:  {a_resp!r}")
                print()


if __name__ == "__main__":
    main()
