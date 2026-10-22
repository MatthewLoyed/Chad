"""
analyze_trace.py — Harness Health Analyzer
==========================================
Reads the audit markdown from perfect_trace.py and the probe JSON from
trace_probe.py, applies coded heuristics, and writes a compact harness
health card that any agent can read and act on in ~1-2k tokens.

Usage:
    python scripts/analyze_trace.py
    python scripts/analyze_trace.py --audit docs/logs/perfect_trace_audit.md
    python scripts/analyze_trace.py --probe tests/data/probe_XXXXXXXX.json
    python scripts/analyze_trace.py --audit <audit.md> --probe <probe.json> --out <card.md>
"""

import json
import re
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime


# ── Path Setup ───────────────────────────────────────────────────────────────

def find_project_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent, here.parent.parent, here.parent.parent.parent]:
        if (candidate / "requirements.txt").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return here.parent


PROJECT_ROOT = find_project_root()


# ── Heuristic Thresholds ─────────────────────────────────────────────────────

SYSTEM_PROMPT_WARN_CHARS  = 6_000
SYSTEM_PROMPT_BLOAT_CHARS = 8_000
RAG_LOW_SCORE_THRESHOLD   = 0.05
RAG_HIGH_SCORE_THRESHOLD  = 0.30
RESPONSE_SHORT_CHARS      = 10

UNCERTAINTY_PHRASES = [
    "i'm not sure", "i am not sure", "i don't know", "i do not know",
    "unclear", "not certain", "i'm unsure", "i cannot determine",
    "i'm confused", "i'm not certain", "the instructions say", "but the user",
    "conflicting", "i should not", "i shouldn't", "i'm hesitant",
]


# ── Markdown Parsers ─────────────────────────────────────────────────────────

def _between(text: str, start_marker: str, end_marker: str = None) -> str:
    """Extract text between two markers (returns empty string if not found)."""
    start = text.find(start_marker)
    if start == -1:
        return ""
    start += len(start_marker)
    if end_marker:
        end = text.find(end_marker, start)
        return text[start:end].strip() if end != -1 else text[start:].strip()
    return text[start:].strip()


def parse_audit(audit_text: str) -> dict:
    d = {}

    # ── System prompt size + health ──
    size_match = re.search(r"\*\*Size:\*\* ([\d,]+) chars(?:\s+\(~([\d,]+)\s+tokens\))?\s*\|\s*\*\*Health:\*\*\s*(.+)", audit_text)
    if size_match:
        d["sys_prompt_chars"] = int(size_match.group(1).replace(",", ""))
        d["sys_prompt_tokens"] = int(size_match.group(2).replace(",", "")) if size_match.group(2) else round(d["sys_prompt_chars"] / 4)
        d["sys_prompt_health"] = size_match.group(3).strip()
    else:
        d["sys_prompt_chars"] = 0
        d["sys_prompt_tokens"] = 0
        d["sys_prompt_health"] = "unknown"

    # ── System prompt content ──
    sys_block = re.search(
        r"## 📜 Active System Prompt.*?```text\n(.*?)```",
        audit_text, re.DOTALL
    )
    d["sys_prompt_text"] = sys_block.group(1) if sys_block else ""

    # ── Bound tools ──
    tools_match = re.search(r"\*\*(\d+)\s+tools\*\*\s+bound", audit_text)
    d["bound_tools_count"] = int(tools_match.group(1)) if tools_match else 0

    schema_size_match = re.search(r"schema size:\s*([\d,]+)\s*chars,\s*~([\d,]+)\s*tokens", audit_text)
    if schema_size_match:
        d["tools_chars"] = int(schema_size_match.group(1).replace(",", ""))
        d["tools_tokens"] = int(schema_size_match.group(2).replace(",", ""))
    else:
        d["tools_chars"] = 0
        d["tools_tokens"] = 0

    tool_rows = re.findall(r"\| `([^`]+)` \| (.+?) \|", audit_text)
    # Filter out header rows
    d["bound_tools"] = [
        {"name": n, "desc": desc.strip()}
        for n, desc in tool_rows
        if n not in ("Tool Name", "Rank", "Check", "Parameter")
    ]

    # ── RAG scores ──
    rag_section = _between(audit_text, "### 🔍 Vector Search (RAG) Matches", "###")
    rag_rows = re.findall(
        r"\| (\d+) \| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \| (.+?) \|",
        rag_section
    )
    d["rag_matches"] = [
        {"rank": int(r), "name": n, "score": _try_float(s), "source": src, "decision": dec.strip()}
        for r, n, s, src, dec in rag_rows
    ]

    # ── Pipeline dispatch ──
    dispatch = _between(audit_text, "### ⚙️ Pipeline Dispatch Logic", "##")
    injector_count_match = re.search(r"\*\*Total Final Bound Schemas\*\*: `(\d+)`", dispatch)
    d["injector_reported_count"] = int(injector_count_match.group(1)) if injector_count_match else None
    forced_match = re.search(r"\*\*Forced Core Tools\*\*: (.+)", dispatch)
    d["forced_core_tools"] = forced_match.group(1).strip() if forced_match else "unknown"
    complexity_match = re.search(r"\*\*Complexity Threshold\*\*: `(.+?)`", dispatch)
    d["complexity"] = complexity_match.group(1) if complexity_match else "unknown"

    # ── Reasoning text ──
    reasoning_block = re.search(
        r"### 🧠 Reasoning / Thinking:\n```text\n(.*?)```",
        audit_text, re.DOTALL
    )
    d["reasoning_text"] = reasoning_block.group(1).strip() if reasoning_block else ""
    d["reasoning_missing"] = "> *(Not captured" in audit_text and not d["reasoning_text"]

    # ── Tool invocations in turn flow ──
    tool_invocations = re.findall(r"🎯 \*\*Tool Invocations:\*\*.*?- `([^`]+)`", audit_text, re.DOTALL)
    d["tool_invocations"] = tool_invocations

    # ── Final response ──
    response_block = re.search(
        r"### Text Response:\n```text\n(.*?)```",
        audit_text, re.DOTALL
    )
    d["final_response"] = response_block.group(1).strip() if response_block else ""

    return d


def _try_float(s: str) -> float:
    try:
        return float(s.strip("`"))
    except (ValueError, AttributeError):
        return 0.0


# ── Heuristics ───────────────────────────────────────────────────────────────

def run_heuristics(audit: dict, probe: dict) -> list:
    """
    Returns a list of findings: {"level": "CRITICAL|WARNING|INFO|OK", "code": str, "message": str}
    """
    findings = []

    def add(level, code, message):
        findings.append({"level": level, "code": code, "message": message})

    # ── System Prompt ────────────────────────────────────────────────────────

    chars = audit.get("sys_prompt_chars", 0)
    tokens = audit.get("sys_prompt_tokens", 0) or round(chars / 4)
    if chars == 0:
        add("CRITICAL", "SYS_MISSING", "System prompt not found in trace — harness may not be injecting it")
    elif chars >= SYSTEM_PROMPT_BLOAT_CHARS:
        add("WARNING", "SYS_BLOATED",
            f"System prompt is {chars:,} chars (~{tokens:,} tokens) (threshold: {SYSTEM_PROMPT_BLOAT_CHARS:,}). "
            f"Bloated prompts dilute instruction weight and slow inference.")
    elif chars >= SYSTEM_PROMPT_WARN_CHARS:
        add("INFO", "SYS_LARGE",
            f"System prompt is {chars:,} chars (~{tokens:,} tokens) — approaching bloat threshold ({SYSTEM_PROMPT_BLOAT_CHARS:,}).")
    else:
        add("OK", "SYS_SIZE", f"System prompt size is healthy ({chars:,} chars, ~{tokens:,} tokens).")

    # Memory section check — must be an actual heading, not a backtick-quoted reference in instructions.
    # Real injection: "### PERSONAL CONTEXT" or "### RELEVANT RECALLED MEMORIES"
    sys_text = audit.get("sys_prompt_text", "")
    has_actual_memories = bool(re.search(
        r"^###\s+(RELEVANT RECALLED MEMORIES|PERSONAL CONTEXT)",
        sys_text, re.MULTILINE
    ))
    if sys_text and not has_actual_memories:
        add("WARNING", "MEMORY_ABSENT",
            "No actual '### PERSONAL CONTEXT' or '### RELEVANT RECALLED MEMORIES' heading in system prompt. "
            "The harness describes where memories go but none were injected this turn. "
            "Either no memories matched this prompt, or memory retrieval did not run.")
    elif sys_text:
        # Check if the memory block explicitly reports that no memories were found
        if "No memories relevant to the prompt were found." in sys_text:
            add("OK", "MEMORY_PRESENT", "Memory retrieval ran successfully, but no matching memories were found (returned default empty status).")
        else:
            add("OK", "MEMORY_PRESENT", "Memory injection section found with active memories.")

    # Duplicate tool listing check (tools in prose AND in schemas)
    if sys_text and "AVAILABLE ATOMIC TOOLS FOR THIS TURN" in sys_text:
        add("INFO", "TOOLS_DUAL_LISTED",
            "Tools appear in both the bound schema payload AND as prose at the end of the system prompt. "
            "The model receives its tool list twice — consider removing the prose copy.")

    # ── Tool Selection ───────────────────────────────────────────────────────

    rag = audit.get("rag_matches", [])
    bound_count = audit.get("bound_tools_count", 0)
    injector_count = audit.get("injector_reported_count")

    if bound_count == 0:
        add("CRITICAL", "NO_TOOLS_BOUND",
            "Zero tools bound in the API payload. Model has no tools available — pure text mode only.")
    else:
        add("OK", "TOOLS_BOUND", f"{bound_count} tools bound in API payload.")

    if injector_count is not None and bound_count > 0 and injector_count == 0:
        add("INFO", "INJECTOR_TELEMETRY_GAP",
            f"ToolRegistryInjector telemetry reports 0 schemas but {bound_count} tools are actually bound. "
            f"This is a known observability gap — the injector trace runs outside the LangGraph context.")

    if rag:
        max_score = max(m["score"] for m in rag)
        if max_score < RAG_LOW_SCORE_THRESHOLD:
            add("WARNING", "RAG_LOW_CONFIDENCE",
                f"All RAG scores are very low (max: {max_score:.4f}, threshold: {RAG_LOW_SCORE_THRESHOLD}). "
                f"Tool selection may be unreliable — embeddings may need reindexing.")
        elif max_score < RAG_HIGH_SCORE_THRESHOLD:
            add("INFO", "RAG_MODERATE",
                f"RAG scores are moderate (max: {max_score:.4f}). Tool selection is working but not confident.")
        else:
            add("OK", "RAG_CONFIDENT", f"RAG top score {max_score:.4f} — tool selection is confident.")
    else:
        add("INFO", "RAG_NO_DATA", "No RAG match data found — semantic tool selector may not have run.")

    # ── Reasoning ────────────────────────────────────────────────────────────

    reasoning = audit.get("reasoning_text", "").lower()

    if audit.get("reasoning_missing"):
        add("WARNING", "REASONING_NOT_CAPTURED",
            "Reasoning/thinking tokens not found in LangSmith output. "
            "This run may predate the lc_wrapper reasoning-persistence fix, "
            "or the model does not support extended thinking.")
    elif not reasoning:
        add("INFO", "REASONING_EMPTY",
            "Model produced no reasoning tokens for this turn (thinking may be disabled or prompt was trivial).")
    else:
        add("OK", "REASONING_PRESENT", f"Reasoning captured ({len(reasoning)} chars).")

        # Uncertainty / confusion signals
        found_uncertainty = [p for p in UNCERTAINTY_PHRASES if p in reasoning]
        if found_uncertainty:
            add("WARNING", "REASONING_UNCERTAIN",
                f"Model expressed uncertainty or confusion in reasoning. "
                f"Phrases found: {found_uncertainty}. "
                f"This often indicates conflicting or ambiguous system prompt instructions.")

        # Probe tag contamination
        if "probe:" in reasoning or "[probe" in reasoning:
            add("INFO", "PROBE_TAG_VISIBLE",
                "Model noticed the [PROBE:...] tag in its reasoning. "
                "This is harmless noise but confirms the tag reaches the model context.")

    # Check reasoning from live stream (probe JSON)
    stream_reasoning = probe.get("reasoning_full", "") if probe else ""
    if stream_reasoning and not reasoning:
        add("WARNING", "REASONING_STREAM_ONLY",
            f"Reasoning was captured from the live WebSocket stream ({len(stream_reasoning)} chars) "
            f"but is absent from LangSmith. The lc_wrapper persistence fix may not be active on the running server.")

    if stream_reasoning:
        stream_lower = stream_reasoning.lower()
        stream_uncertain = [p for p in UNCERTAINTY_PHRASES if p in stream_lower]
        if stream_uncertain and not audit.get("reasoning_text"):
            add("WARNING", "STREAM_REASONING_UNCERTAIN",
                f"Stream reasoning shows uncertainty: {stream_uncertain}. "
                f"Likely a system prompt instruction conflict.")

    # ── Response ─────────────────────────────────────────────────────────────

    response = audit.get("final_response", "")
    tool_invocations = audit.get("tool_invocations", [])
    ws_tool_calls = probe.get("tool_calls", []) if probe else []

    if not response or not response.strip():
        add("CRITICAL", "RESPONSE_EMPTY",
            "Final response is empty or whitespace. The model completed the turn but returned nothing.")
    elif len(response) < RESPONSE_SHORT_CHARS:
        add("WARNING", "RESPONSE_SHORT",
            f"Final response is suspiciously short ({len(response)} chars): '{response}'. "
            f"Model may have produced a tool call with no follow-up text.")
    else:
        add("OK", "RESPONSE_OK", f"Response received ({len(response)} chars).")

    if tool_invocations:
        add("OK", "TOOLS_USED",
            f"Model called {len(tool_invocations)} tool(s): {', '.join(tool_invocations)}.")
    elif bound_count > 0 and response and len(response) > RESPONSE_SHORT_CHARS:
        add("INFO", "NO_TOOLS_USED",
            "Model answered without calling any tools. "
            "Verify this was appropriate for the prompt — tool may have been skipped incorrectly.")

    # WebSocket vs LangSmith tool call consistency
    ws_count = len(ws_tool_calls)
    ls_count = len(tool_invocations)
    if ws_count != ls_count and (ws_count > 0 or ls_count > 0):
        add("INFO", "TOOL_COUNT_MISMATCH",
            f"WebSocket stream saw {ws_count} tool call(s), LangSmith shows {ls_count}. "
            f"Multi-turn LangGraph loops may have multiple LLM calls; "
            f"perfect_trace.py shows the LAST LLM call only.")

    return findings


# ── Report Generation ─────────────────────────────────────────────────────────

def generate_card(audit: dict, probe: dict, findings: list, out_path: Path,
                  prompt: str, run_id: str, probe_tag: str) -> None:

    level_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2, "OK": 3}
    level_icon  = {"CRITICAL": "[CRITICAL]", "WARNING": "[WARNING]", "INFO": "[INFO]", "OK": "[OK]"}
    sorted_findings = sorted(findings, key=lambda f: level_order.get(f["level"], 9))

    criticals = [f for f in findings if f["level"] == "CRITICAL"]
    warnings  = [f for f in findings if f["level"] == "WARNING"]
    infos     = [f for f in findings if f["level"] == "INFO"]
    oks       = [f for f in findings if f["level"] == "OK"]

    overall = "CRITICAL" if criticals else ("WARNING" if warnings else "OK")
    overall_icon = {"CRITICAL": "FAIL", "WARNING": "WARN", "OK": "PASS"}[overall]

    lines = []
    lines.append("# HARNESS HEALTH CARD")
    lines.append(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Prompt    : {prompt}")
    lines.append(f"Probe tag : {probe_tag or 'N/A'}")
    lines.append(f"Run ID    : {run_id or 'N/A'}")
    lines.append(f"Overall   : {overall_icon} ({len(criticals)} critical, {len(warnings)} warning, {len(infos)} info, {len(oks)} ok)")
    lines.append("")

    # ── Quick metrics ──
    lines.append("## METRICS")
    sys_tokens = audit.get("sys_prompt_tokens") or round(audit.get("sys_prompt_chars", 0) / 4)
    lines.append(f"System prompt   : {audit.get('sys_prompt_chars', 0):,} chars (~{sys_tokens:,} tokens) [{audit.get('sys_prompt_health', 'unknown')}]")
    
    tools_chars = audit.get("tools_chars", 0)
    tools_tokens = audit.get("tools_tokens", 0)
    if tools_chars > 0:
        lines.append(f"Bound tools     : {audit.get('bound_tools_count', 0)} tools (schema size: {tools_chars:,} chars, ~{tools_tokens:,} tokens)")
    else:
        lines.append(f"Bound tools     : {audit.get('bound_tools_count', 0)}")

    rag = audit.get("rag_matches", [])
    if rag:
        top = rag[0]
        lines.append(f"Top RAG match   : {top['name']} (score {top['score']:.4f})")
    else:
        lines.append(f"Top RAG match   : N/A")

    reasoning_chars = len(audit.get("reasoning_text", ""))
    stream_chars = len(probe.get("reasoning_full", "")) if probe else 0
    lines.append(f"Reasoning       : {reasoning_chars} chars in LangSmith | {stream_chars} chars from stream")
    lines.append(f"Tool calls      : {len(audit.get('tool_invocations', []))} (LangSmith) | {len(probe.get('tool_calls', [])) if probe else 'N/A'} (stream)")
    response = audit.get("final_response", "")
    lines.append(f"Response        : {len(response)} chars — {repr(response[:80])}")
    lines.append("")

    # ── Reasoning block ──
    reasoning_text = audit.get("reasoning_text", "") or probe.get("reasoning_full", "") if probe else ""
    if reasoning_text:
        lines.append("## REASONING (primary harness signal)")
        lines.append("```")
        lines.append(reasoning_text[:2000])
        if len(reasoning_text) > 2000:
            lines.append(f"... [{len(reasoning_text) - 2000} chars omitted]")
        lines.append("```")
        lines.append("")

    # ── Findings ──
    lines.append("## FINDINGS")
    for f in sorted_findings:
        icon = level_icon[f["level"]]
        lines.append(f"{icon} [{f['code']}]")
        lines.append(f"  {f['message']}")
        lines.append("")

    # ── Active system prompt excerpt ──
    sys_text = audit.get("sys_prompt_text", "")
    if sys_text:
        lines.append("## SYSTEM PROMPT (first 1500 chars)")
        lines.append("```")
        lines.append(sys_text[:1500])
        if len(sys_text) > 1500:
            lines.append(f"... [{len(sys_text) - 1500} chars omitted — full text in perfect_trace_audit.md]")
        lines.append("```")
        lines.append("")

    # ── Agent instructions ──
    lines.append("## AGENT INSTRUCTIONS")
    lines.append("Read the FINDINGS above and produce a harness health report covering:")
    lines.append("1. What the model received from the harness (system prompt, tools, memory)")
    lines.append("2. Whether the reasoning shows confusion caused by harness configuration")
    lines.append("3. Specific lines/sections of the system prompt to fix for each CRITICAL or WARNING")
    lines.append("4. Whether tool selection found the right tools for this prompt type")
    lines.append("5. A confidence rating: HIGH / MEDIUM / LOW that the harness is correctly configured")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[OK] Health card written: {out_path}")
    print(f"[OK] Overall: {overall_icon} — {len(criticals)} critical, {len(warnings)} warning, "
          f"{len(infos)} info, {len(oks)} ok")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Harness health analyzer")
    parser.add_argument("--audit", default=None,
                        help="Path to perfect_trace_audit.md (default: docs/logs/perfect_trace_audit.md)")
    parser.add_argument("--probe", default=None,
                        help="Path to probe_XXXXXXXX.json from trace_probe.py")
    parser.add_argument("--out", default=None,
                        help="Output path for health card (default: docs/logs/harness_health_card.md)")
    args = parser.parse_args()

    audit_path = Path(args.audit) if args.audit else PROJECT_ROOT / "docs" / "logs" / "perfect_trace_audit.md"
    out_path   = Path(args.out)   if args.out   else PROJECT_ROOT / "docs" / "logs" / "harness_health_card.md"

    if not audit_path.exists():
        print(f"[ERROR] Audit file not found: {audit_path}")
        print(f"  Run: python scripts/perfect_trace.py <run_id>")
        sys.exit(1)

    print(f"[INFO] Reading audit: {audit_path}")
    audit_text = audit_path.read_text(encoding="utf-8")
    audit = parse_audit(audit_text)

    # Load probe JSON if available
    probe = None
    if args.probe:
        probe_path = Path(args.probe)
        if probe_path.exists():
            print(f"[INFO] Reading probe: {probe_path}")
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
        else:
            print(f"[WARN] Probe file not found: {probe_path} — skipping stream data")
    else:
        # Auto-detect most recent probe in tests/data/
        probe_dir = PROJECT_ROOT / "tests" / "data"
        if probe_dir.exists():
            probes = sorted(probe_dir.glob("probe_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if probes:
                print(f"[INFO] Auto-detected most recent probe: {probes[0].name}")
                probe = json.loads(probes[0].read_text(encoding="utf-8"))

    # ── Cross-validate probe tag vs audit content ────────────────────────────
    probe_tag = probe.get("trace_tag", "") if probe else ""
    if probe_tag:
        if f"PROBE:{probe_tag}" not in audit_text:
            print(f"\n[ERROR] STALE AUDIT DETECTED")
            print(f"[ERROR]   Probe tag PROBE:{probe_tag} not found anywhere in the audit file.")
            print(f"[ERROR]   The audit is from a different run than the probe JSON.")
            print(f"[ERROR]   Run this first, then re-run analyze_trace.py:")
            if probe.get("langsmith_run_id"):
                print(f"[ERROR]   python scripts/perfect_trace.py {probe['langsmith_run_id']}")
            else:
                print(f"[ERROR]   python scripts/perfect_trace.py --match PROBE:{probe_tag}")
            sys.exit(1)
        else:
            print(f"[OK] Probe tag PROBE:{probe_tag} confirmed in audit — traces match.")

    findings = run_heuristics(audit, probe)

    # Extract metadata for the card header
    prompt   = probe.get("prompt", "unknown") if probe else "unknown"
    run_id   = None

    # Extract run ID from audit metadata table
    run_id_match = re.search(r"\*\*Run ID\*\* \| `([^`]+)`", audit_text)
    if run_id_match:
        run_id = run_id_match.group(1)

    # Warn if audit run ID doesn't match the one stored in probe (if present)
    if run_id and probe and probe.get("langsmith_run_id") and run_id != probe["langsmith_run_id"]:
        print(f"[WARN] Run ID mismatch: audit has {run_id}, probe recorded {probe['langsmith_run_id']}")

    generate_card(audit, probe, findings, out_path, prompt, run_id, probe_tag)


if __name__ == "__main__":
    main()
