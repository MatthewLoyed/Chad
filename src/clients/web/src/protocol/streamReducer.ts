/**
 * Phase 1 step 3: the single stream reducer.
 * ==========================================
 *
 * `applyStreamEvent(state, event)` folds the typed StreamEvent union (see
 * ./streamEvents.ts) into ONE immutable turn state: an ordered list of typed
 * parts (reasoning / text / tool / plan) plus turn-level lifecycle/telemetry.
 *
 * This is the inverse of today's design, where the same token is reassembled in
 * 5+ places with different merge rules (turnEventReducer, store.addThinkingEvent,
 * ThinkingWidget.displayEvents, AIResponseRenderer.buildTree, gui_limb
 * _consolidate_and_sanitize_thoughts). Here, a part is keyed by id and can only
 * grow — flicker and cutoff are structurally impossible.
 *
 * Not yet wired into the live app. It is proven against the Phase 0 wall via the
 * legacy-frame bridge (testkit/legacyFrameBridge.ts) before any production swap —
 * see docs/features/STREAMING_REFACTOR.md, Phase 1 step 4.
 */
import { detectSeqGap, assertNever, type StreamEvent, type BrowserProgressPayload } from './streamEvents';
import type { PlanStep, TokenUsage, ThinkingEvent } from '../store';

/** A reasoning ("thinking") part — id'd, append-only, finalized by reasoning_end. */
export interface ReasoningPart {
    kind: 'reasoning';
    id: string;
    text: string;
    ended: boolean;
    agent_name?: string;
    /** Delegation correlation id — groups this reasoning under a subagent panel. */
    parent_id?: string;
    startedAt?: number;
    endedAt?: number;
    durationMs?: number;
}

/** A final-answer text part — id'd, append-only, finalized by text_end. */
export interface TextPart {
    kind: 'text';
    id: string;
    text: string;
    ended: boolean;
}

/** A tool call — start carries args; end fills in ok/result. */
export interface ToolPart {
    kind: 'tool';
    id: string;
    name: string;
    args?: Record<string, unknown>;
    display_text?: string;
    parent_id?: string;
    ok?: boolean;
    result?: unknown;
    download_token?: string;
    filename?: string;
    youtube_video_id?: string;
    youtube_title?: string;
    auto_preview?: boolean;
    awaiting_user?: boolean;
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

/** An execution plan — replace-semantics snapshot, one card per plan_id. */
export interface PlanPart {
    kind: 'plan';
    id: string;
    goal?: string;
    steps: PlanStep[];
}

/** Resolved per-turn tool menu — replace-semantics snapshot for wait-theater UI. */
export interface ToolMenuPart {
    kind: 'tool_menu';
    id: string;
    tools: string[];
    shortlist_size?: number;
    selection_path?: string;
}

export interface BrowserProgressPart {
    kind: 'browser_progress';
    id: string;
    progress: BrowserProgressPayload;
}
export type TurnPart = ReasoningPart | TextPart | ToolPart | PlanPart | ToolMenuPart | BrowserProgressPart;

export type TurnLifecycle = 'idle' | 'running' | 'finished' | 'error';

/** The whole turn as one immutable value the renderer reads top-to-bottom. */
export interface TurnState {
    turn_id: string | null;
    /** Ordered render list: reasoning/text/tool/plan parts in arrival order. */
    parts: TurnPart[];
    lifecycle: TurnLifecycle;
    usage: TokenUsage | null;
    /** Full answer text for persistence, set on run_finished. */
    response: string | null;
    error: string | null;
    /** Last seq seen this turn (for gap detection); null before the first event. */
    lastSeq: number | null;
    /** True once a seq gap/reorder was detected — the renderer can surface a soft error. */
    seqGap: boolean;
}

export function initialTurnState(): TurnState {
    return {
        turn_id: null,
        parts: [],
        lifecycle: 'idle',
        usage: null,
        response: null,
        error: null,
        lastSeq: null,
        seqGap: false,
    };
}

/** Find the open (not-yet-ended) part of a kind with a given id. */
function findOpenPart<K extends TurnPart['kind']>(
    parts: TurnPart[],
    kind: K,
    id: string,
): Extract<TurnPart, { kind: K }> | undefined {
    return parts.find(
        (p): p is Extract<TurnPart, { kind: K }> => p.kind === kind && p.id === id,
    );
}

/**
 * Fold one event into the turn state, returning a NEW state (parts are copied on
 * write; untouched parts keep their identity so React can bail out of re-rendering
 * them). Pure: no DOM, no store, no side effects.
 */
export function applyStreamEvent(state: TurnState, event: StreamEvent): TurnState {
    // Gap detection is advisory — we still apply the event, but flag the tear so
    // the UI can request a resync rather than silently render a torn stream.
    const gap = detectSeqGap(state.lastSeq, event);
    const base: TurnState = {
        ...state,
        lastSeq: event.seq,
        seqGap: state.seqGap || !gap.ok,
    };

    switch (event.type) {
        case 'run_started':
            return {
                ...initialTurnState(),
                turn_id: event.turn_id,
                lifecycle: 'running',
                lastSeq: event.seq,
            };

        case 'reasoning_delta': {
            const open = findOpenPart(base.parts, 'reasoning', event.part_id);
            if (open && !open.ended) {
                return {
                    ...base,
                    parts: base.parts.map((p) =>
                        p === open ? { ...open, text: open.text + event.delta } : p,
                    ),
                };
            }
            const now = Date.now();
            const part: ReasoningPart = {
                kind: 'reasoning',
                id: event.part_id,
                text: event.delta,
                ended: false,
                agent_name: event.agent_name,
                parent_id: event.parent_id,
                startedAt: now,
            };
            return { ...base, parts: [...base.parts, part] };
        }

        case 'reasoning_end': {
            const now = Date.now();
            return {
                ...base,
                parts: base.parts.map((p) => {
                    if (p.kind === 'reasoning' && p.id === event.part_id) {
                        const started = p.startedAt ?? now;
                        return {
                            ...p,
                            ended: true,
                            endedAt: now,
                            durationMs: p.durationMs ?? Math.max(0, now - started),
                        };
                    }
                    return p;
                }),
            };
        }

        case 'text_delta': {
            const open = findOpenPart(base.parts, 'text', event.part_id);
            if (open && !open.ended) {
                return {
                    ...base,
                    parts: base.parts.map((p) =>
                        p === open ? { ...open, text: open.text + event.delta } : p,
                    ),
                };
            }
            const part: TextPart = { kind: 'text', id: event.part_id, text: event.delta, ended: false };
            return { ...base, parts: [...base.parts, part] };
        }

        case 'text_end':
            return {
                ...base,
                parts: base.parts.map((p) =>
                    p.kind === 'text' && p.id === event.part_id ? { ...p, ended: true } : p,
                ),
            };

        case 'tool_start': {
            const part: ToolPart = {
                kind: 'tool',
                id: event.tool_call_id,
                name: event.name,
                args: event.args,
                display_text: event.display_text,
                parent_id: event.parent_id,
            };
            return { ...base, parts: [...base.parts, part] };
        }

        case 'tool_end':
            return {
                ...base,
                parts: base.parts.map((p) =>
                    p.kind === 'tool' && p.id === event.tool_call_id
                        ? {
                              ...p,
                              ok: event.ok,
                              result: event.result,
                              download_token: event.download_token,
                              filename: event.filename,
                              youtube_video_id: event.youtube_video_id,
                              youtube_title: event.youtube_title,
                              auto_preview: event.auto_preview,
                              awaiting_user: event.awaiting_user ?? p.awaiting_user,
                              map_view: event.map_view,
                          }
                        : p,
                ),
            };

        case 'browser_progress': {
            const part: BrowserProgressPart = { kind: 'browser_progress', id: event.progress.run_id, progress: event.progress };
            const exists = base.parts.some(p => p.kind === 'browser_progress' && p.id === part.id);
            return { ...base, parts: exists ? base.parts.map(p => p.kind === 'browser_progress' && p.id === part.id ? part : p) : [...base.parts, part] };
        }
        case 'plan_snapshot': {
            // Replace semantics: a plan_id renders exactly one card; later snapshots
            // overwrite goal/steps wholesale (the "single plan card" invariant).
            const existing = findOpenPart(base.parts, 'plan', event.plan_id);
            if (existing) {
                return {
                    ...base,
                    parts: base.parts.map((p) =>
                        p === existing ? { ...existing, goal: event.goal, steps: event.steps } : p,
                    ),
                };
            }
            const part: PlanPart = { kind: 'plan', id: event.plan_id, goal: event.goal, steps: event.steps };
            return { ...base, parts: [...base.parts, part] };
        }

        case 'tools_snapshot': {
            const existing = findOpenPart(base.parts, 'tool_menu', event.menu_id);
            if (existing) {
                return {
                    ...base,
                    parts: base.parts.map((p) =>
                        p === existing
                            ? {
                                  ...existing,
                                  tools: event.tools,
                                  shortlist_size: event.shortlist_size,
                                  selection_path: event.selection_path,
                              }
                            : p,
                    ),
                };
            }
            const part: ToolMenuPart = {
                kind: 'tool_menu',
                id: event.menu_id,
                tools: event.tools,
                shortlist_size: event.shortlist_size,
                selection_path: event.selection_path,
            };
            return { ...base, parts: [...base.parts, part] };
        }

        case 'token_usage':
            return { ...base, usage: event.usage };

        case 'run_finished': {
            const now = Date.now();
            return {
                ...base,
                lifecycle: 'finished',
                response: event.response ?? base.response,
                parts: base.parts.map((p) => {
                    if (p.kind === 'reasoning' && !p.ended) {
                        const started = p.startedAt ?? now;
                        return {
                            ...p,
                            ended: true,
                            endedAt: now,
                            durationMs: p.durationMs ?? Math.max(0, now - started),
                        };
                    }
                    if (p.kind === 'text' && !p.ended) {
                        return { ...p, ended: true };
                    }
                    return p;
                }),
            };
        }

        case 'run_error':
            return { ...base, lifecycle: 'error', error: event.message };

        default:
            return assertNever(event);
    }
}

/** Fold a whole sequence of events from the initial state. */
export function reduceStreamEvents(events: StreamEvent[]): TurnState {
    return events.reduce(applyStreamEvent, initialTurnState());
}

/** All reasoning text the user would read, in order. */
export function reasoningText(state: TurnState): string {
    return state.parts.filter((p): p is ReasoningPart => p.kind === 'reasoning').map((p) => p.text).join('');
}

/** All answer text the user would read, in order. */
export function answerText(state: TurnState): string {
    return state.parts.filter((p): p is TextPart => p.kind === 'text').map((p) => p.text).join('');
}

/** The tool parts in arrival order. */
export function toolParts(state: TurnState): ToolPart[] {
    return state.parts.filter((p): p is ToolPart => p.kind === 'tool');
}

/** The plan parts (≤ 1 per plan_id) in arrival order. */
export function planParts(state: TurnState): PlanPart[] {
    return state.parts.filter((p): p is PlanPart => p.kind === 'plan');
}

/**
 * Adapt a reduced TurnState into the flat `ThinkingEvent[]` the existing
 * AIResponseRenderer/ThinkingWidget already consume. This is the migration seam:
 * it lets the proven reducer become the SINGLE source feeding render without first
 * rewriting the render tree — the production swap is just feeding the renderer
 * `partsToThinkingEvents(streamTurn)` instead of `activeTurn.events`.
 *
 * Mapping (mirrors the legacy event shapes buildTree expects):
 *  - reasoning part → one `reasoning` event (already coalesced; the renderer groups
 *    consecutive reasoning into a thinking block).
 *  - text part      → one `speech` event (a finalized answer block).
 *  - tool part      → one `tool_start` event carrying the resolved success/result
 *    inline (buildTree merges tool_end onto tool_start by correlation_id; emitting
 *    the already-merged node is equivalent and avoids a second event).
 *  - plan part      → one `execution_plan` event (replace semantics already applied).
 */
export function partsToThinkingEvents(state: TurnState): ThinkingEvent[] {
    return state.parts.map((p): ThinkingEvent => {
        switch (p.kind) {
            case 'browser_progress':
                return { type: 'browser_progress', browserProgress: p.progress, parent_id: p.progress.parent_id ?? undefined };
            case 'reasoning':
                return {
                    type: 'reasoning',
                    text: p.text,
                    agent_name: p.agent_name,
                    parent_id: p.parent_id,
                    append: true,
                    durationMs: p.durationMs,
                    timestamp: p.startedAt,
                };
            case 'text':
                return { type: 'speech', text: p.text };
            case 'tool':
                return {
                    type: 'tool_start',
                    correlation_id: p.id,
                    name: p.name,
                    params: p.args,
                    prompt: typeof p.args?.prompt === 'string'
                        ? p.args.prompt
                        : typeof p.args?.task === 'string'
                        ? p.args.task
                        : undefined,
                    display_text: p.display_text,
                    parent_id: p.parent_id,
                    success: p.ok,
                    result: typeof p.result === 'string' ? p.result : p.result != null ? JSON.stringify(p.result) : undefined,
                    download_token: p.download_token,
                    filename: p.filename,
                    youtube_video_id: p.youtube_video_id,
                    youtube_title: p.youtube_title,
                    auto_preview: p.auto_preview,
                    awaiting_user: p.awaiting_user,
                    map_view: p.map_view,
                };
            case 'plan':
                return { type: 'execution_plan', plan: p.steps, plan_id: p.id, goal: p.goal };
            case 'tool_menu':
                return {
                    type: 'tool_menu',
                    tools: p.tools,
                    shortlist_size: p.shortlist_size,
                    selection_path: p.selection_path,
                };
            default:
                return assertNever(p);
        }
    });
}
