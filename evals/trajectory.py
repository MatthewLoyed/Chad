"""Shared eval trajectory packaging — one builder for live, rejudge, and all rubrics.

Judges and transcripts should not each reinvent summary vs raw formatting.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence


def _event_agent(ev: Dict[str, Any]) -> str:
    return str(ev.get("agent") or ev.get("agent_name") or "?")


def build_trajectory_summary(
    *,
    events: Optional[Sequence[Dict[str, Any]]] = None,
    tool_names: Optional[Sequence[str]] = None,
    plan: Optional[list] = None,
    gather_brief: Optional[str] = None,
    stop_reason: Optional[str] = None,
    response: Optional[str] = None,
) -> str:
    """Compact structured summary of a turn (for the top of a judge prompt)."""
    events = list(events or [])
    names = list(tool_names or [])
    if not names:
        names = [e.get("name") for e in events if e.get("type") == "tool_start" and e.get("name")]

    thinking_agents = Counter(
        _event_agent(e) for e in events if e.get("type") == "thinking"
    )
    tool_counts = Counter(names)
    scout_n = sum(1 for n in names if n == "scout_gather")
    dispatch_n = sum(1 for n in names if n == "dispatch_scout")
    propose_n = sum(1 for n in names if n == "propose_plan")

    # Duplicate file_id reads + title-like ids from tool params
    file_ids: List[str] = []
    failed_reads = 0
    for e in events:
        if e.get("type") == "tool_start" and e.get("name") == "google_drive_read_file":
            params = e.get("params") or {}
            fid = params.get("file_id")
            if fid:
                file_ids.append(str(fid))
        if e.get("type") == "tool_end" and e.get("name") == "google_drive_read_file":
            res = str(e.get("result") or "")
            if "File not found" in res or "'success': False" in res or '"success": false' in res:
                failed_reads += 1

    id_counts = Counter(file_ids)
    dupes = {k: v for k, v in id_counts.items() if v > 1}
    # Real Drive ids are long and typically start with '1'. Flag titles / short tokens.
    title_like = [
        i for i in file_ids
        if (" " in i) or (not i.startswith("1")) or ("_" in i and len(i) < 40)
    ]

    lines = [
        f"stop_reason: {stop_reason or '(none)'}",
        f"plan_steps: {len(plan) if plan else 0}",
        f"gather_brief_chars: {len(gather_brief or '')}",
        f"response_chars: {len(response or '')}",
        f"tool_count: {len(names)}",
        f"scout_gather_count: {scout_n}",
        f"dispatch_scout_count: {dispatch_n}",
        f"propose_plan_count: {propose_n}",
        f"thinking_by_agent: {dict(thinking_agents)}",
        f"tools: {dict(tool_counts)}",
        f"duplicate_file_id_reads: {dupes or '{}'}",
        f"title_like_file_ids: {title_like or []}",
        f"failed_drive_reads: {failed_reads}",
    ]
    return "\n".join(lines)


def _fmt_value(val: Any, *, max_chars: int = 0) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        text = json.dumps(val, indent=2, default=str)
    else:
        text = str(val)
    if max_chars and max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + f"\n... [{len(text) - max_chars} chars truncated]"
    return text


def format_full_trajectory(
    events: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    gather_brief: Optional[str] = None,
    plan: Optional[list] = None,
    max_event_chars: int = 0,
) -> str:
    """Chronological raw trajectory: thoughts, tools, bodies — organized by event order."""
    events = list(events or [])
    parts: List[str] = []
    if gather_brief:
        parts.append("### GATHER_BRIEF (final)\n" + gather_brief)
    if plan:
        parts.append("### PLAN\n" + _fmt_value(plan, max_chars=max_event_chars))

    for i, e in enumerate(events):
        et = e.get("type") or "?"
        if et == "thinking":
            agent = _event_agent(e)
            text = e.get("text") or ""
            parts.append(
                f"### [{i}] THINKING ({agent})\n{_fmt_value(text, max_chars=max_event_chars)}"
            )
        elif et == "tool_start":
            name = e.get("name") or "?"
            params = e.get("params") or {}
            parts.append(
                f"### [{i}] TOOL_START `{name}`\n"
                f"params:\n{_fmt_value(params, max_chars=max_event_chars)}"
            )
        elif et == "tool_end":
            name = e.get("name") or "?"
            result = e.get("result")
            content = e.get("content")
            body = content if content not in (None, "") else result
            parts.append(
                f"### [{i}] TOOL_END `{name}`\n"
                f"{_fmt_value(body, max_chars=max_event_chars)}"
            )
        elif et == "plan":
            parts.append(
                f"### [{i}] PLAN_EVENT\n"
                f"goal: {e.get('goal') or ''}\n"
                f"gather_brief_chars: {len(e.get('gather_brief') or '')}\n"
                f"plan:\n{_fmt_value(e.get('plan'), max_chars=max_event_chars)}"
            )
        else:
            parts.append(f"### [{i}] {et}\n{_fmt_value(e, max_chars=max_event_chars)}")

    return "\n\n".join(parts) if parts else "(no events)"


def pack_judge_evidence(
    *,
    prompt: str,
    events: Optional[Sequence[Dict[str, Any]]] = None,
    tool_names: Optional[Sequence[str]] = None,
    plan: Optional[list] = None,
    gather_brief: Optional[str] = None,
    stop_reason: Optional[str] = None,
    response: Optional[str] = None,
    max_event_chars: int = 0,
) -> str:
    """SUMMARY + FULL TRAJECTORY for judge user messages."""
    summary = build_trajectory_summary(
        events=events,
        tool_names=tool_names,
        plan=plan,
        gather_brief=gather_brief,
        stop_reason=stop_reason,
        response=response,
    )
    full = format_full_trajectory(
        events,
        gather_brief=gather_brief,
        plan=plan,
        max_event_chars=max_event_chars,
    )
    return (
        f"USER REQUEST:\n{prompt}\n\n"
        f"## SUMMARY\n{summary}\n\n"
        f"## FULL TRAJECTORY\n{full}"
    )
