/**
 * Idempotent turn-event reducer.
 * All live thinking/plan/tool events flow through here before React renders them.
 */
import type { ThinkingEvent } from '../store';

const MERGEABLE_TYPES = new Set(['thought', 'thinking', 'reasoning', 'speech']);
const MAX_SEEN_EVENT_IDS = 256;

export interface ActiveTurn {
    events: ThinkingEvent[];
    /** Server-assigned event ids already applied (replay / double-delivery guard). */
    seenEventIds: string[];
}

export interface TurnReducerResult {
    activeTurn: ActiveTurn;
    /** Whether the incoming event was ignored as a duplicate. */
    skipped: boolean;
}

export const emptyActiveTurn = (): ActiveTurn => ({
    events: [],
    seenEventIds: [],
});

const rememberEventId = (seenEventIds: string[], id: string): string[] => {
    const next = [...seenEventIds, id];
    return next.length > MAX_SEEN_EVENT_IDS ? next.slice(-MAX_SEEN_EVENT_IDS) : next;
};

const upsertExecutionPlan = (events: ThinkingEvent[], incoming: ThinkingEvent): ThinkingEvent[] => {
    // One plan slot per turn — mismatched plan_id values must not create a second card.
    const existingIdx = events.findIndex((e) => e.type === 'execution_plan');
    if (existingIdx === -1) {
        return [...events, incoming];
    }

    const existing = events[existingIdx];
    const merged: ThinkingEvent = {
        ...incoming,
        plan_id: incoming.plan_id ?? existing.plan_id,
        goal: incoming.goal ?? existing.goal,
    };
    const next = [...events];
    next[existingIdx] = merged;
    return next;
};

const mergeWithLast = (events: ThinkingEvent[], incoming: ThinkingEvent): ThinkingEvent[] => {
    const last = events[events.length - 1];
    if (!last || last.type !== incoming.type || !MERGEABLE_TYPES.has(incoming.type)) {
        return [...events, incoming];
    }

    const shouldAppend =
        incoming.append || incoming.type === 'reasoning' || incoming.type === 'thought';

    const next = [...events];
    next[next.length - 1] = {
        ...last,
        text: shouldAppend
            ? (last.text || '') + (incoming.text || '')
            : incoming.text,
        timestamp: last.timestamp ?? incoming.timestamp,
        durationMs: incoming.durationMs ?? last.durationMs,
        id: incoming.id ?? last.id,
        sequence: incoming.sequence ?? last.sequence,
    };
    return next;
};

/** Keep at most one execution_plan block per turn (last update wins). */
export const dedupeExecutionPlans = (events: ThinkingEvent[]): ThinkingEvent[] => {
    const planIndices = events
        .map((e, i) => (e.type === 'execution_plan' ? i : -1))
        .filter((i) => i >= 0);
    if (planIndices.length <= 1) return events;
    const keepIdx = planIndices[planIndices.length - 1];
    return events.filter((e, i) => e.type !== 'execution_plan' || i === keepIdx);
};

/**
 * Apply one thinking event to the active turn.
 * Safe to call repeatedly with the same server `id` — duplicates are ignored.
 */
export function applyTurnEvent(
    activeTurn: ActiveTurn | null,
    incoming: ThinkingEvent,
): TurnReducerResult {
    if (incoming.id && activeTurn?.seenEventIds.includes(incoming.id)) {
        return { activeTurn: activeTurn ?? emptyActiveTurn(), skipped: true };
    }

    let turn = activeTurn ?? emptyActiveTurn();
    if (incoming.id) {
        turn = { ...turn, seenEventIds: rememberEventId(turn.seenEventIds, incoming.id) };
    }

    if (incoming.type === 'turn_start') {
        return {
            activeTurn: {
                events: [incoming],
                seenEventIds: incoming.id ? [incoming.id] : [],
            },
            skipped: false,
        };
    }

    let events = turn.events;

    if (incoming.type === 'execution_plan') {
        events = upsertExecutionPlan(events, incoming);
    } else if (MERGEABLE_TYPES.has(incoming.type)) {
        events = mergeWithLast(events, incoming);
    } else {
        events = [...events, incoming];
    }

    return {
        activeTurn: { ...turn, events },
        skipped: false,
    };
}

/** Normalize a persisted thought list (history hydration / message finalize). */
export function normalizeTurnEvents(events: ThinkingEvent[]): ThinkingEvent[] {
    return dedupeExecutionPlans(events);
}
