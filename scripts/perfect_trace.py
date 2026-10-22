"""
Perfect Trace — LangSmith Audit Extractor
==========================================
Fetches the most recent matching LLM run from LangSmith, extracts the full
payload (system prompt, tools, messages, output), and generates a structured
markdown diagnostic report.

Designed for token efficiency: fetches only 5 recent runs, retries up to 3
times with a short delay for LangSmith ingestion lag.
"""
import os
import sys
import json
import re
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from langsmith import Client

# ─── Path Setup ─────────────────────────────────────────────────────────────
# scripts/perfect_trace.py → scripts/ is parent, project root is parents[1]
script_path = Path(__file__).resolve()
project_root = script_path.parents[1]
dotenv_path = project_root / ".env"

if dotenv_path.exists():
    load_dotenv(str(dotenv_path))
else:
    load_dotenv()

client = Client()
project_name = os.getenv("LANGCHAIN_PROJECT", "ChadAi")

# ─── Constants ──────────────────────────────────────────────────────────────
FETCH_LIMIT = 5       # Token-efficient: only check the most recent few runs
MAX_RETRIES = 3       # Retry for LangSmith ingestion delay
RETRY_DELAY_S = 3.0   # Seconds between retries


def log(level, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level:>5}] {msg}", flush=True)


# ─── Run Fetching ───────────────────────────────────────────────────────────

def get_matching_run(text_match=None):
    """
    Fetch the most recent LLM run, optionally filtered by text_match.
    Retries up to MAX_RETRIES times to handle LangSmith ingestion lag.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        runs = list(client.list_runs(
            project_name=project_name,
            run_type="llm",
            limit=FETCH_LIMIT,
        ))

        for r in runs:
            inputs = r.inputs or {}
            messages = str(inputs.get("messages", ""))

            if text_match and text_match.lower() not in messages.lower():
                continue

            if r.outputs or (r.inputs and r.inputs.get("messages")):
                log("OK", f"Found matching run: {r.name} ({r.id}) on attempt {attempt}")
                return r

        if attempt < MAX_RETRIES:
            log("WARN", f"No matching run found (attempt {attempt}/{MAX_RETRIES}). "
                        f"Retrying in {RETRY_DELAY_S}s...")
            time.sleep(RETRY_DELAY_S)

    return None


# ─── Bound Tools Parsing ───────────────────────────────────────────────────

def parse_bound_tools(params):
    """
    Extract bound tools from invocation params. Tries multiple strategies:
    1. OpenAI-format 'tools' or 'functions' keys (structured, reliable)
    2. Regex fallback on string representation (legacy StructuredTool, BaseTool)
    """
    tools = []
    seen = set()
    total_chars = 0

    def _add(name, desc=""):
        if name and name not in seen:
            seen.add(name)
            truncated = (desc[:120] + "...") if len(desc) > 120 else desc
            tools.append({"name": name, "desc": truncated})

    # Strategy 1: OpenAI-format tool schemas (most reliable)
    for key in ("tools", "functions"):
        entries = params.get(key, [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    # Measure character size of the schema
                    total_chars += len(json.dumps(entry))
                    # OpenAI tools format: {"type": "function", "function": {"name": ..., "description": ...}}
                    func = entry.get("function", entry)
                    _add(func.get("name", ""), func.get("description", ""))

    # Strategy 2: Regex fallback on stringified invocation params
    if not tools:
        invoc_str = str(params)
        # Match StructuredTool, BaseTool, Tool, etc.
        # Robust match that handles internal parenthesis inside descriptions by checking for subsequent tools or collection ends
        tool_blocks = re.findall(r"(?:Structured|Base)?Tool\((.*?)\)(?=\s*(?:,\s*(?:Structured|Base)?Tool|\]|\Z))", invoc_str, re.DOTALL)
        for block in tool_blocks:
            total_chars += len(block)
            # Robust non-greedy captures with boundaries to handle nested quotes and special escapes
            n_match = re.search(r"name=\\?(['\"])(.*?)\\?\1", block)
            d_match = re.search(r"description=\\?(['\"])(.*?)\\?\1(?=, args_schema=|, coroutine=|\))", block, re.DOTALL)
            name = n_match.group(2) if n_match else None
            desc = d_match.group(2) if d_match else ""
            _add(name, desc)

    return tools, total_chars


# ─── Message Type Resolution ───────────────────────────────────────────────

def resolve_message_type(m):
    """
    Robustly determine message type from a LangSmith message dict.
    Uses a fallback chain instead of fragile id[3] indexing.
    """
    # Priority 1: Explicit 'type' key (skip LangSmith serialization types)
    LANGSMITH_INTERNAL_TYPES = {"constructor", "not_implemented"}
    raw_type = m.get("type", "")
    if isinstance(raw_type, str) and raw_type and raw_type not in LANGSMITH_INTERNAL_TYPES:
        return raw_type

    # Priority 2: LangSmith list-format ID where index 3 is the message type
    # e.g. ["langchain_core", "messages", "system", "SystemMessage"]
    m_id = m.get("id")
    if isinstance(m_id, list) and len(m_id) > 3 and isinstance(m_id[3], str):
        return m_id[3]

    # Priority 3: Infer from structure
    kwargs = m.get("kwargs", {})
    if kwargs.get("tool_call_id"):
        return "ToolMessage"
    if kwargs.get("tool_calls"):
        return "AIMessage"
    if "role" in kwargs:
        role_map = {"system": "SystemMessage", "user": "HumanMessage",
                    "assistant": "AIMessage", "tool": "ToolMessage"}
        return role_map.get(kwargs["role"], "Unknown")

    return "Unknown"


def extract_tool_diagnostics(trace_runs):
    """
    Extract custom tool diagnostic data from the trace run tree.
    Scans for 'ToolRegistryInjector' and 'SemanticToolSelector' spans.
    """
    injector_run = next((r for r in trace_runs if r.name == "ToolRegistryInjector"), None)
    selector_run = next((r for r in trace_runs if r.name == "SemanticToolSelector"), None)
    rag_searches = [r for r in trace_runs if r.name == "SupabaseRAGSearch"]

    data = {
        "available": False,
        "user_query": None,
        "is_simple": None,
        "injected_schemas": [],
        "rag_matches": [],
        "sub_queries": []
    }

    if not injector_run and not selector_run:
        return data

    data["available"] = True

    if injector_run:
        inj_inputs = injector_run.inputs or {}
        inj_outputs = injector_run.outputs or {}
        data["user_query"] = inj_inputs.get("query")

        out_val = inj_outputs.get("output", {})
        if isinstance(out_val, dict):
            data["is_simple"] = out_val.get("is_simple")
            data["injected_schemas"] = [s.get("name") for s in out_val.get("schemas", []) if isinstance(s, dict)]

    if selector_run:
        sel_outputs = selector_run.outputs or {}
        out_val = sel_outputs.get("output", [])
        if isinstance(out_val, list):
            data["rag_matches"] = out_val

    for sr in rag_searches:
        sr_inputs = sr.inputs or {}
        sr_outputs = sr.outputs or {}
        q = sr_inputs.get("query")
        if q:
            out_val = sr_outputs.get("output", [])
            matches = []
            if isinstance(out_val, list):
                matches = [item.get("name") for item in out_val if isinstance(item, dict)]
            data["sub_queries"].append({
                "query": q,
                "hits": matches
            })

    return data


# ─── Report Generation ─────────────────────────────────────────────────────

def generate_report(run_id=None, text_match=None, out_suffix=""):
    if run_id:
        try:
            run = client.read_run(run_id)
        except Exception as e:
            log("ERROR", f"Could not fetch run ID {run_id}: {e}")
            return False
    else:
        run = get_matching_run(text_match=text_match)

    if not run:
        qualifier = f" matching '{text_match}'" if text_match else ""
        log("ERROR", f"No run found{qualifier} in the last {FETCH_LIMIT} runs.")
        return False

    log("INFO", f"Generating audit for run: {run.name} ({run.id})")

    inputs = run.inputs or {}
    outputs = run.outputs or {}
    params = run.extra.get("invocation_params", {}) if run.extra else {}
    bound_tools, tools_chars = parse_bound_tools(params)

    # Fetch parent trace tree for semantic middleware observability
    trace_id = run.trace_id
    trace_runs = []
    if trace_id:
        try:
            trace_runs = list(client.list_runs(trace_id=trace_id))
        except Exception as te:
            log("WARN", f"Could not fetch trace tree: {te}")

    # FIX: ToolRegistryInjector runs outside the LangGraph runtime context and records as an independent Root Trace.
    # We query the 30 most recent runs in the project to locate and merge the matching selection telemetry span.
    try:
        recent_project_runs = list(client.list_runs(project_name=project_name, limit=30))

        # Match injector trace by content (Query/Tag) to avoid stale data cross-pollination
        injector_root = None
        for r in recent_project_runs:
            if r.name == "ToolRegistryInjector":
                r_inputs = r.inputs or {}
                # If we have a text_match (like TRACE_ID), prioritize matching it
                if text_match and text_match.lower() in str(r_inputs.get("query", "")).lower():
                    injector_root = r
                    break
                # Fallback: if no match provided, take the absolute most recent one
                if not text_match:
                    injector_root = r
                    break

        if injector_root:
            injector_trace = list(client.list_runs(trace_id=injector_root.id))
            trace_runs.extend(injector_trace)
            log("OK", f"Retrieved separated ToolRegistryInjector trace: {injector_root.id}")
    except Exception as ie:
        log("WARN", f"Could not query separate injector trace: {ie}")

    tool_diag = extract_tool_diagnostics(trace_runs)

    md = []

    # ── Header ───────────────────────────────────────────────────────────
    md.append("# 🔍 CHAD EXTRACE: Native Diagnostic Report")
    md.append(f"*Audit generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    md.append("")
    md.append("> [!IMPORTANT]")
    md.append("> This is UNBIASED ground-truth trace data from LangSmith. It captures "
              "exactly what the LLM API received and returned, bypassing all middleware.")
    md.append("")

    # ── Isolation Verification ───────────────────────────────────────────
    all_input_text = str(inputs.get("messages", ""))
    trace_found = text_match and text_match.lower() in all_input_text.lower() if text_match else None

    md.append("## 🔒 Isolation Verification")
    if text_match:
        status = "✅ CONFIRMED" if trace_found else "❌ NOT FOUND"
        md.append(f"| Check | Result |")
        md.append(f"| :--- | :--- |")
        md.append(f"| **Trace Tag** | `{text_match}` |")
        md.append(f"| **Found in Messages** | {status} |")
        if not trace_found:
            md.append("")
            md.append("> [!WARNING]")
            md.append("> The isolation tag was NOT found in this run's messages. "
                      "This trace may belong to a different session.")
    else:
        md.append("*No isolation tag provided — using most recent run.*")
    md.append("")

    # ── Metadata ─────────────────────────────────────────────────────────
    latency = str(run.end_time - run.start_time) if run.end_time else "N/A"
    md.append("## 📌 Execution Metadata")
    md.append("| Parameter | Value |")
    md.append("| :--- | :--- |")
    md.append(f"| **Run ID** | `{run.id}` |")
    md.append(f"| **Run Name** | `{run.name}` |")
    md.append(f"| **Project** | `{project_name}` |")
    md.append(f"| **Timestamp (UTC)** | `{run.start_time}` |")
    md.append(f"| **Latency** | `{latency}` |")
    md.append("")

    # ── Bound Tools ──────────────────────────────────────────────────────
    md.append("## 🛠️ Bound Tools Configuration")
    if bound_tools:
        tools_tokens = round(tools_chars / 4)
        md.append(f"**{len(bound_tools)} tools** bound in the native API payload (schema size: {tools_chars:,} chars, ~{tools_tokens:,} tokens):")
        md.append("")
        md.append("| Tool Name | Description |")
        md.append("| :--- | :--- |")
        for t in bound_tools:
            md.append(f"| `{t['name']}` | {t['desc']} |")
    else:
        md.append("> ⚠️ **NO TOOLS DETECTED**: The model ran in zero-tool conversational mode.")
    md.append("")

    # ── Tool Selection & Routing Diagnostic ───────────────────────────────
    md.append("## 🧭 Tool Selection & Routing Diagnostic")
    if tool_diag["available"]:
        # 1. Sub-Intents (Query Decomposition)
        if tool_diag["sub_queries"]:
            md.append("### 🧩 Query Decomposition")
            md.append("The user query was split into the following sub-intents for parallel vector search:")
            md.append("")
            for sq in tool_diag["sub_queries"]:
                q_text = sq["query"]
                hits = sq["hits"]
                hits_str = ", ".join([f"`{h}`" for h in hits]) if hits else "*No matches*"
                md.append(f"- **Sub-Intent**: `{q_text}`  \n  🎯 *Raw Vector Hits:* {hits_str}")
            md.append("")

        # 2. Merged RAG Scores Table
        md.append("### 🔍 Vector Search (RAG) Matches")
        if tool_diag["rag_matches"]:
            md.append("| Rank | Tool Name | Score | Source | Decision |")
            md.append("| :--- | :--- | :--- | :--- | :--- |")

            injected_set = set(tool_diag["injected_schemas"])

            for r_idx, match in enumerate(tool_diag["rag_matches"]):
                name = match.get("name")
                score = match.get("similarity", 0.0)
                source = match.get("source", "Supabase")

                if injected_set and name in injected_set:
                    status = "✅ INJECTED"
                elif injected_set:
                    status = "❌ PRUNED (Over cap)"
                else:
                    status = "➖ N/A"

                score_str = f"`{score:.4f}`" if isinstance(score, (int, float)) else f"`{score}`"
                md.append(f"| {r_idx + 1} | `{name}` | {score_str} | `{source}` | {status} |")
        else:
            md.append("*No semantic matches returned for this query.*")
        md.append("")

        # 3. Final Pipeline Breakdown
        md.append("### ⚙️ Pipeline Dispatch Logic")
        is_simple_str = "Simple (Minimal Thinking)" if tool_diag["is_simple"] else "Complex (Deep Thinking)"
        if tool_diag["is_simple"] is None:
            is_simple_str = "*(Not determined)*"

        md.append(f"- **Intended Query**: \"{tool_diag['user_query']}\"")
        md.append(f"- **Complexity Threshold**: `{is_simple_str}`")

        rag_set = set([m.get("name") for m in tool_diag["rag_matches"]])
        core_forced = [s for s in tool_diag["injected_schemas"] if s not in rag_set]
        core_forced_str = ", ".join([f"`{c}`" for c in core_forced]) if core_forced else "*None*"
        md.append(f"- **Forced Core Tools**: {core_forced_str}")

        total_inj = len(tool_diag["injected_schemas"])
        md.append(f"- **Total Final Bound Schemas**: `{total_inj}` tools")
        md.append("")
    else:
        md.append("*(Observability span data not available for this run. Run must be executed after the pipeline was instrumented.)*")
        md.append("")


    # ── Parse Messages ───────────────────────────────────────────────────
    all_messages = []
    msg_payload = inputs.get("messages")
    if msg_payload and isinstance(msg_payload, list):
        flat = msg_payload[0] if msg_payload and isinstance(msg_payload[0], list) else msg_payload
        for m in flat:
            if isinstance(m, dict):
                m_type = resolve_message_type(m)
                kw = m.get("kwargs", {})
                all_messages.append({
                    "type": m_type,
                    "content": kw.get("content", ""),
                    "tool_calls": kw.get("tool_calls", []),
                })

    # ── System Prompt ────────────────────────────────────────────────────
    sys_prompt = next((m["content"] for m in all_messages if m["type"] == "SystemMessage"), None)
    md.append("## 📜 Active System Prompt")
    if sys_prompt:
        # Measure prompt size for health check
        prompt_chars = len(sys_prompt)
        prompt_tokens = round(prompt_chars / 4)
        prompt_health = "🟢 Normal" if prompt_chars < 4000 else "🟡 Large" if prompt_chars < 8000 else "🔴 Bloated"
        md.append(f"**Size:** {prompt_chars:,} chars (~{prompt_tokens:,} tokens) | **Health:** {prompt_health}")
        md.append("```text")
        md.append(sys_prompt)
        md.append("```")
    else:
        md.append("> ⚠️ No SystemMessage found in this run's input payload.")
    md.append("")

    # ── Turn Flow ────────────────────────────────────────────────────────
    md.append("## 💬 Turn Execution Flow")
    md.append(f"**{len(all_messages)} messages** in the payload:")
    md.append("")

    role_emojis = {
        "SystemMessage": "⚙️", "HumanMessage": "🧑‍💻",
        "AIMessage": "🤖", "ToolMessage": "🔧",
    }

    for idx, m in enumerate(all_messages):
        emoji = role_emojis.get(m["type"], "❓")
        md.append(f"### [{idx}] {emoji} {m['type']}")

        text = str(m["content"]).strip()
        if text:
            # Identity check: Suppress redundancy if it matches the active system prompt printed at the top
            if m["type"] == "SystemMessage" and text == str(sys_prompt).strip():
                md.append("*(Identical to 'Active System Prompt' rendered in full at the top)*")
            elif len(text) < 5000:
                if (text.startswith("{") and text.endswith("}")) or \
                   (text.startswith("[") and text.endswith("]")):
                    try:
                        md.append("```json")
                        md.append(json.dumps(json.loads(text), indent=2))
                        md.append("```")
                    except (json.JSONDecodeError, ValueError):
                        md.append(f"```text\n{text}\n```")
                else:
                    md.append(f"```text\n{text}\n```")
            else:
                # Truncate very long messages to save tokens
                md.append(f"```text\n{text[:2000]}\n... [TRUNCATED — {len(text):,} chars total]\n```")
        else:
            md.append("*(Empty payload)*")

        if m["tool_calls"]:
            md.append("")
            md.append("🎯 **Tool Invocations:**")
            for tc in m["tool_calls"]:
                md.append(f"- `{tc.get('name')}` →")
                md.append("  ```json")
                md.append(json.dumps(tc.get("args", {}), indent=2))
                md.append("  ```")
        md.append("")
        md.append("---")
        md.append("")

    # ── Final Output ─────────────────────────────────────────────────────
    md.append("## 📤 Final Model Response")

    # Extract from LangSmith generations (standard path)
    content = ""
    tool_calls = []
    reasoning_text = ""
    if outputs:
        gens = outputs.get("generations", [])
        if gens and isinstance(gens, list) and gens[0]:
            gen = gens[0][0] if isinstance(gens[0], list) else gens[0]
            msg = gen.get("message", {})
            kw = msg.get("kwargs", {}) if isinstance(msg, dict) else {}
            content = kw.get("content", "")
            tool_calls = kw.get("tool_calls", [])
            extra_kw = kw.get("additional_kwargs", {})
            reasoning_text = extra_kw.get("reasoning", "")

    # Fallback: when generations is empty (e.g. turn ended via ask_user_question
    # or another tool-routed terminator), read the last AIMessage from the turn flow.
    if not content and not tool_calls and all_messages:
        last_ai = next(
            (m for m in reversed(all_messages) if m["type"] == "AIMessage" and m.get("content", "").strip()),
            None
        )
        if last_ai:
            content = last_ai["content"]
            tool_calls = last_ai.get("tool_calls", [])
            md.append("> *(Response sourced from last AIMessage in turn flow — "
                      "LangSmith `generations` was empty, likely a tool-terminated turn)*")
            md.append("")

    # Reasoning/thinking text — primary signal for conceptual errors.
    # Populated by both adapters via GenerateResponse.reasoning → lc_wrapper additional_kwargs.
    if reasoning_text and reasoning_text.strip():
        REASONING_LIMIT = 3000
        md.append("### 🧠 Reasoning / Thinking:")
        display = reasoning_text
        truncated = False
        if len(reasoning_text) > REASONING_LIMIT:
            display = reasoning_text[:REASONING_LIMIT]
            truncated = True
        md.append(f"```text\n{display}\n```")
        if truncated:
            md.append(f"*[{len(reasoning_text) - REASONING_LIMIT:,} chars hidden — full text in raw run]*")
        md.append("")
    else:
        md.append("### 🧠 Reasoning / Thinking:")
        md.append("> *(Not captured — model may not support extended thinking, "
                  "or run predates the reasoning persistence fix)*")
        md.append("")

    if content and content.strip():
        md.append("### Text Response:")
        md.append(f"```text\n{content}\n```")
        md.append("")
    if tool_calls:
        md.append("### Tool Actions:")
        for t in tool_calls:
            md.append(f"- `{t.get('name')}` →")
            md.append("  ```json")
            md.append(json.dumps(t.get("args", {}), indent=2))
            md.append("  ```")
    elif not (content and content.strip()):
        if outputs is not None:
            md.append("*(Empty output payload — turn may have ended silently)*")
        else:
            md.append("*(Outputs not yet finalized in LangSmith — ingestion may be delayed)*")

    # ── Write Report ─────────────────────────────────────────────────────
    out_dir = project_root / "docs" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix_str = f"_{out_suffix}" if out_suffix else ""
    out_path = out_dir / f"perfect_trace_audit{suffix_str}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    log("OK", f"Audit written to: {out_path.relative_to(project_root)}")
    return True


# ─── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Perfect Trace — LangSmith Audit Extractor")
    parser.add_argument("run_id", nargs="?", help="Exact LangSmith Run ID to audit.")
    parser.add_argument("--match", help="Substring to match in recent run messages (e.g. TRACE_ID).")
    parser.add_argument("--out-suffix", default="", help="Optional suffix to append to the output filename (e.g. '001').")
    args = parser.parse_args()

    success = generate_report(run_id=args.run_id, text_match=args.match, out_suffix=args.out_suffix)
    sys.exit(0 if success else 1)
