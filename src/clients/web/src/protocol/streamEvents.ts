/**
 * Phase 1 keystone: the unified, typed, id-correlated stream protocol.
 * ====================================================================
 *
 * Target wire contract shared Python (emitter) ↔ TS (reducer), modelled on the
 * AG-UI / Vercel AI SDK approach: every stream is start→delta→end, each carrying
 * a stable id; deltas accumulate into one immutable ordered list of typed parts
 * via a single reducer — no manual DOM, no stringly-typed `state` ladder.
 *
 * This file is the SPEC-IN-CODE. It is intentionally not yet wired into the live
 * app (see docs/features/STREAMING_REFACTOR.md, Phase 1 step 1). The Python
 * emitter (step 2), the reducer `applyStreamEvent` (step 3), and the legacy-frame
 * bridge (step 4) build against these types so equivalence can be proven against
 * the Phase 0 wall before any production swap.
 *
 * Invariants every event upholds:
 *   - `turn_id`  groups all events of one turn.
 *   - `seq`      is monotonic within a turn; gaps are detectable (see detectSeqGap).
 *   - delta events (`*_delta`) carry incremental text keyed by `part_id`.
 *   - `*_end` finalizes a part; a part's text can only grow, never shrink.
 *   - snapshot events (`plan_snapshot`) replace prior state for that id wholesale.
 */
import type { PlanStep, TokenUsage } from '../store';

/** Fields on every event. */
export interface StreamEventBase {
    /** Groups all events belonging to one turn. */
    turn_id: string;
    /** Monotonic sequence within the turn; the client detects gaps/reorder. */
    seq: number;
}

/** Turn lifecycle — start. */
export interface RunStarted extends StreamEventBase {
    type: 'run_started';
}

/** Reasoning ("thinking") stream — kept separate from the answer text stream. */
export interface ReasoningDelta extends StreamEventBase {
    type: 'reasoning_delta';
    /** Stable id of the reasoning part this delta appends to. */
    part_id: string;
    delta: string;
    /** Optional sub-agent attribution for nested reasoning. */
    agent_name?: string;
    /** Delegation correlation id grouping this reasoning under a subagent panel. */
    parent_id?: string;
}
export interface ReasoningEnd extends StreamEventBase {
    type: 'reasoning_end';
    part_id: string;
}

/** Final-answer text stream. */
export interface TextDelta extends StreamEventBase {
    type: 'text_delta';
    part_id: string;
    delta: string;
}
export interface TextEnd extends StreamEventBase {
    type: 'text_end';
    part_id: string;
}

/** Tool call — start carries args, end carries the outcome. */
export interface ToolStart extends StreamEventBase {
    type: 'tool_start';
    tool_call_id: string;
    name: string;
    args?: Record<string, unknown>;
    /** Human-readable action label, e.g. "Searching the web…". */
    display_text?: string;
    /** Parent tool/agent id for nested (delegated) calls. */
    parent_id?: string;
}
export interface ToolEnd extends StreamEventBase {
    type: 'tool_end';
    tool_call_id: string;
    ok: boolean;
    result?: unknown;
    download_token?: string;
    filename?: string;
    youtube_video_id?: string;
    youtube_title?: string;
    /** When true, open the artifact preview panel after a verified HTML artifact. */
    auto_preview?: boolean;
    awaiting_user?: boolean;
    /** Inline OpenStreetMap places card payload from show_map. */
    map_view?: {
        title: string;
        center: { lat: number; lon: number };
        zoom: number;
        places: Array<{
            name: string;
            lat: number;
            lon: number;
            category?: string;
            address?: string;
            osm_url?: string;
        }>;
    };
}

/** Execution plan — replace-semantics snapshot keyed by plan_id (one card per turn). */
export interface PlanSnapshot extends StreamEventBase {
    type: 'plan_snapshot';
    plan_id: string;
    goal?: string;
    steps: PlanStep[];
}

/** Resolved tool menu — replace-semantics snapshot for wait-theater chip carousel. */
export interface ToolsSnapshot extends StreamEventBase {
    type: 'tools_snapshot';
    menu_id: string;
    tools: string[];
    shortlist_size?: number;
    selection_path?: string;
}

/** Token telemetry — may fire mid-stream and at turn end. */
export interface TokenUsageEvent extends StreamEventBase {
    type: 'token_usage';
    usage: TokenUsage;
}

/** Turn lifecycle — success. `response` is the full answer text for persistence. */
export interface RunFinished extends StreamEventBase {
    type: 'run_finished';
    response?: string;
    stop_reason?: string;
    turn_duration_ms?: number;
}

/** Turn lifecycle — failure. */
export interface RunError extends StreamEventBase {
    type: 'run_error';
    message: string;
    code?: string;
}

/** The full discriminated union. */
export interface BrowserProgressPayload {
    run_id: string;
    status: 'running' | 'awaiting_approval' | 'completed' | 'blocked' | 'incomplete';
    milestone: string;
    subgoal: string;
    reason: string;
    actions: number;
    recoveries: number;
    parent_id?: string | null;
}
export interface BrowserProgressEvent extends StreamEventBase {
    type: 'browser_progress';
    progress: BrowserProgressPayload;
}

export type StreamEvent =
    | BrowserProgressEvent
    | RunStarted
    | ReasoningDelta
    | ReasoningEnd
    | TextDelta
    | TextEnd
    | ToolStart
    | ToolEnd
    | PlanSnapshot
    | ToolsSnapshot
    | TokenUsageEvent
    | RunFinished
    | RunError;

export type StreamEventType = StreamEvent['type'];

/**
 * `Omit<StreamEvent, K>` does NOT distribute over the union — `keyof StreamEvent`
 * collapses to only the keys common to every member, dropping variant-specific
 * fields like `part_id`/`tool_call_id`. This distributes Omit over each member first.
 */
export type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/** Exhaustiveness helper — `assertNever(x)` fails to compile if a case is unhandled. */
export function assertNever(x: never): never {
    throw new Error(`Unhandled stream event: ${JSON.stringify(x)}`);
}

export interface SeqGapResult {
    /** True if `event.seq` is exactly prevSeq + 1 (or this is the first event). */
    ok: boolean;
    /** Number of missed sequence numbers (0 when ok, >0 on gap, <0 on reorder/dupe). */
    missed: number;
}

/**
 * Detect a sequence gap or reorder. Pass `prevSeq = null` for the first event of a
 * turn. A gap (missed > 0) means the transport dropped events and the client should
 * request a resync / surface a soft error rather than render a torn stream.
 */
export function detectSeqGap(prevSeq: number | null, event: StreamEventBase): SeqGapResult {
    if (prevSeq === null) {
        return { ok: true, missed: 0 };
    }
    const missed = event.seq - prevSeq - 1;
    return { ok: missed === 0, missed };
}

/* ────────────────────────────────────────────────────────────────────────────
 * Legacy wire → StreamEvent mapping (reference for the Phase 1 bridge, step 4).
 * Current frame (window.handle*)                  →  StreamEvent
 * ────────────────────────────────────────────────────────────────────────────
 *  {type:'run_started'}                            →  run_started
 *  {type:'reasoning'|'thought'|'thinking', text,   →  reasoning_delta {part_id, delta,
 *      append:true, agent_name?, parent_id?}             agent_name?, parent_id?}
 *  caption(text, append:true)                      →  text_delta {part_id, delta}
 *  {type:'speech', append:false}                   →  text_end (+ commit) for that part
 *  {type:'tool_start', correlation_id, name,       →  tool_start {tool_call_id, ...}
 *      params, display_text}
 *  {type:'tool_end', correlation_id, success,      →  tool_end {tool_call_id, ok, result}
 *      result}
 *  activity_snapshot PLAN {message_id, content}    →  plan_snapshot {plan_id, steps, goal}
 *  activity_snapshot TOOLS {message_id, content}   →  tools_snapshot {menu_id, tools, ...}
 *  handleTokenUsage(usage)                         →  token_usage {usage}
 *  {type:'run_finished', response}                 →  run_finished {response}
 *  ERROR / SYSTEM_ERROR                            →  run_error {message}
 *  handleStateUpdate('ready'|'speaking'|...)       →  (derived from lifecycle; no
 *                                                      explicit event — Phase 2 state
 *                                                      machine owns status)
 * ──────────────────────────────────────────────────────────────────────────── */
