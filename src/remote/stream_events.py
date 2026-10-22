"""
Phase 1 step 2: the Python typed stream emitter (Brain→Limb seam).
==================================================================

This is the producing half of the unified stream protocol whose consuming half
is the TS reducer ``src/clients/web/src/protocol/streamReducer.ts``. It emits the
exact same discriminated union defined in
``src/clients/web/src/protocol/streamEvents.ts`` — every event carries ``turn_id``
and a monotonic ``seq``; reasoning/text are separate id'd ``start→delta→end``
streams; plans are replace-semantics snapshots.

It replaces the stringly-typed ``state`` if/elif ladder in ``gui_limb.py`` /
``main_window.push_thinking_event`` as the *source of truth* for streaming. The
part-boundary rules deliberately MIRROR the TS legacy-frame bridge
(``testkit/legacyFrameBridge.ts``) so the two languages agree on how a turn
decomposes into parts — that bridge's equivalence suite is the cross-check.

Pure and dependency-free: methods take the logical signals ``handle_message``
already decodes and return a list of plain ``dict`` events ready to hand to the
Limb (``window.applyStreamEvent``). No I/O, no webview, no globals — so it is
unit-testable without a running Brain or GUI.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# A StreamEvent is a plain JSON-serializable dict; we keep it loose (dict) on the
# Python side rather than a dataclass-per-variant so it crosses evaluate_js as-is
# and stays trivially comparable in tests.
StreamEvent = Dict[str, Any]

# Reasoning frame "states" from the Brain that map to reasoning_delta.
_REASONING_STATES = frozenset({"reasoning", "thought", "thinking", "thought_reasoning"})


class StreamEventEmitter:
    """
    Stateful per-turn emitter. One instance spans one turn; call :meth:`run_started`
    to (re)initialize. Mirrors the TS bridge's cursor exactly:

    - Consecutive reasoning deltas coalesce into ONE reasoning part; any
      non-reasoning event (tool_start / tool_end / plan / speech finalize) closes
      it, so the next reasoning delta opens a fresh part. A change of
      ``agent_name``/``parent_id`` also closes the open part so subagent handoffs
      (Scout → Planner) do not merge thoughts across panels.
    - Text deltas accumulate into the current text part; a speech finalize ends it
      and the next text delta opens a new one.
    - PLAN snapshots collapse to a single per-turn ``plan_id`` (replace semantics).
    - A turn that streams no text and carries its answer only in the final response
      synthesizes one text part from that response.
    """

    def __init__(self, turn_id: str = "turn") -> None:
        self._turn_id = turn_id
        self._seq = 0
        self._reasoning_part_id: Optional[str] = None
        self._reasoning_count = 0
        self._reasoning_agent_name: Optional[str] = None
        self._reasoning_parent_id: Optional[str] = None
        self._text_part_id: Optional[str] = None
        self._text_count = 0

    # -- internals ----------------------------------------------------------

    def _emit(self, event: StreamEvent) -> StreamEvent:
        event = {**event, "turn_id": self._turn_id, "seq": self._seq}
        self._seq += 1
        return event

    def _close_reasoning(self, out: List[StreamEvent]) -> None:
        """End the open reasoning part, if any (called when a non-reasoning event arrives)."""
        if self._reasoning_part_id is not None:
            out.append(self._emit({"type": "reasoning_end", "part_id": self._reasoning_part_id}))
            self._reasoning_part_id = None
            self._reasoning_agent_name = None
            self._reasoning_parent_id = None

    # -- lifecycle ----------------------------------------------------------

    def run_started(self, turn_id: Optional[str] = None) -> List[StreamEvent]:
        """Begin a turn. Resets all part cursors; ``turn_id`` re-keys the stream."""
        if turn_id:
            self._turn_id = turn_id
        self._seq = 0
        self._reasoning_part_id = None
        self._reasoning_count = 0
        self._reasoning_agent_name = None
        self._reasoning_parent_id = None
        self._text_part_id = None
        self._text_count = 0
        return [self._emit({"type": "run_started"})]

    def run_finished(self, response: Optional[str] = None, stop_reason: Optional[str] = None,
                     turn_duration_ms: Optional[int] = None) -> List[StreamEvent]:
        """End a turn. Closes open parts; synthesizes a text part from ``response``
        when nothing was streamed (the response-only answer case)."""
        out: List[StreamEvent] = []
        self._close_reasoning(out)
        if self._text_part_id is not None:
            out.append(self._emit({"type": "text_end", "part_id": self._text_part_id}))
            self._text_part_id = None
        if self._text_count == 0 and response:
            part_id = f"text-{self._text_count}"
            self._text_count += 1
            out.append(self._emit({"type": "text_delta", "part_id": part_id, "delta": response}))
            out.append(self._emit({"type": "text_end", "part_id": part_id}))
        finished: StreamEvent = {"type": "run_finished"}
        if response is not None:
            finished["response"] = response
        if stop_reason is not None:
            finished["stop_reason"] = stop_reason
        if turn_duration_ms is not None:
            finished["turn_duration_ms"] = turn_duration_ms
        out.append(self._emit(finished))
        return out

    def run_error(self, message: str, code: Optional[str] = None) -> List[StreamEvent]:
        evt: StreamEvent = {"type": "run_error", "message": message}
        if code is not None:
            evt["code"] = code
        return [self._emit(evt)]

    def run_cancelled(self) -> List[StreamEvent]:
        """User stopped the turn — close open parts and signal cancellation.

        Delegates to ``run_finished`` with ``stop_reason='user_cancelled'`` so the
        reducer sets ``lifecycle='finished'`` and the frontend commit path fires
        exactly as it does for a normal turn end.
        """
        return self.run_finished(response=None, stop_reason="user_cancelled")

    # -- reasoning ----------------------------------------------------------

    def reasoning_delta(self, text: str, agent_name: Optional[str] = None,
                        parent_id: Optional[str] = None) -> List[StreamEvent]:
        """Append a reasoning delta, opening a new reasoning part if one isn't open.

        ``agent_name``/``parent_id`` attribute the reasoning to a subagent: ``parent_id``
        is the delegation tool's correlation id (the grouping key) and ``agent_name`` the
        display label, so the UI can isolate a subagent's thoughts into its own panel.
        A change of either field closes the open part so handoffs do not merge panels.
        """
        if not text:
            return []
        out: List[StreamEvent] = []
        if (
            self._reasoning_part_id is not None
            and (agent_name != self._reasoning_agent_name or parent_id != self._reasoning_parent_id)
        ):
            self._close_reasoning(out)
        if self._reasoning_part_id is None:
            self._reasoning_part_id = f"reasoning-{self._reasoning_count}"
            self._reasoning_count += 1
            self._reasoning_agent_name = agent_name
            self._reasoning_parent_id = parent_id
        evt: StreamEvent = {"type": "reasoning_delta", "part_id": self._reasoning_part_id, "delta": text}
        if agent_name:
            evt["agent_name"] = agent_name
        if parent_id:
            evt["parent_id"] = parent_id
        out.append(self._emit(evt))
        return out

    # -- answer text --------------------------------------------------------

    def text_delta(self, text: str) -> List[StreamEvent]:
        """Append an answer-text delta (a streamed caption), opening a part if needed."""
        if not text:
            return []
        if self._text_part_id is None:
            self._text_part_id = f"text-{self._text_count}"
            self._text_count += 1
        return [self._emit({"type": "text_delta", "part_id": self._text_part_id, "delta": text})]

    def speech_finalize(self, text: Optional[str] = None) -> List[StreamEvent]:
        """
        Finalize the current answer block (the Brain's ``speech`` event). Captions
        before it were the deltas; this ends the part. If no captions opened a part
        (the whole block arrived only here), open one with ``text`` as its single
        delta first.
        """
        out: List[StreamEvent] = []
        self._close_reasoning(out)
        if self._text_part_id is None:
            self._text_part_id = f"text-{self._text_count}"
            self._text_count += 1
            out.append(self._emit({"type": "text_delta", "part_id": self._text_part_id, "delta": text or ""}))
        out.append(self._emit({"type": "text_end", "part_id": self._text_part_id}))
        self._text_part_id = None
        return out

    # -- tools --------------------------------------------------------------

    def tool_start(self, tool_call_id: str, name: str, args: Optional[Dict[str, Any]] = None,
                   display_text: Optional[str] = None, parent_id: Optional[str] = None) -> List[StreamEvent]:
        out: List[StreamEvent] = []
        self._close_reasoning(out)
        evt: StreamEvent = {"type": "tool_start", "tool_call_id": tool_call_id, "name": name}
        if args is not None:
            evt["args"] = args
        if display_text is not None:
            evt["display_text"] = display_text
        if parent_id is not None:
            evt["parent_id"] = parent_id
        out.append(self._emit(evt))
        return out

    def tool_end(self, tool_call_id: str, ok: bool, result: Any = None,
                 download_token: Optional[str] = None, filename: Optional[str] = None,
                 youtube_video_id: Optional[str] = None, youtube_title: Optional[str] = None,
                 auto_preview: Optional[bool] = None,
                 awaiting_user: bool = False,
                 map_view: Optional[Dict[str, Any]] = None) -> List[StreamEvent]:
        # Close open reasoning so a following agent (Planner after Scout) opens a fresh part.
        out: List[StreamEvent] = []
        self._close_reasoning(out)
        evt: StreamEvent = {"type": "tool_end", "tool_call_id": tool_call_id, "ok": ok}
        if result is not None:
            evt["result"] = result
        if download_token is not None:
            evt["download_token"] = download_token
        if filename is not None:
            evt["filename"] = filename
        if youtube_video_id is not None:
            evt["youtube_video_id"] = youtube_video_id
        if youtube_title is not None:
            evt["youtube_title"] = youtube_title
        if auto_preview:
            evt["auto_preview"] = True
        if awaiting_user:
            evt["awaiting_user"] = True
        if map_view is not None:
            evt["map_view"] = map_view
        out.append(self._emit(evt))
        return out

    # -- plan & telemetry ---------------------------------------------------

    def browser_progress(self, progress: Dict[str, Any]) -> List[StreamEvent]:
        out: List[StreamEvent] = []
        self._close_reasoning(out)
        out.append(self._emit({'type': 'browser_progress', 'progress': progress}))
        return out

    def plan_snapshot(self, steps: List[Dict[str, Any]], goal: Optional[str] = None) -> List[StreamEvent]:
        """Emit a plan snapshot. All snapshots in a turn share one ``plan_id`` so the
        reducer's replace semantics collapse them to a single card."""
        out: List[StreamEvent] = []
        self._close_reasoning(out)
        evt: StreamEvent = {"type": "plan_snapshot", "plan_id": f"{self._turn_id}-plan", "steps": steps}
        if goal is not None:
            evt["goal"] = goal
        out.append(self._emit(evt))
        return out

    def tools_snapshot(
        self,
        tools: List[str],
        shortlist_size: Optional[int] = None,
        selection_path: Optional[str] = None,
    ) -> List[StreamEvent]:
        """Emit the resolved per-turn tool menu for the wait-theater chip carousel."""
        out: List[StreamEvent] = []
        evt: StreamEvent = {
            "type": "tools_snapshot",
            "menu_id": f"{self._turn_id}-tools",
            "tools": tools,
        }
        if shortlist_size is not None:
            evt["shortlist_size"] = shortlist_size
        if selection_path:
            evt["selection_path"] = selection_path
        out.append(self._emit(evt))
        return out

    def token_usage(self, usage: Dict[str, Any]) -> List[StreamEvent]:
        return [self._emit({"type": "token_usage", "usage": usage})]
