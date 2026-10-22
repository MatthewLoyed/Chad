/**
 * AG-UI-inspired turn protocol normalizer.
 * Converts wire events from Python into internal ThinkingEvent shape for the reducer.
 */
import type { ThinkingEvent } from '../store';

const ORCHESTRATION_RE =
    /(?:TASK_COMPLETED|TASK_FAILED|NEEDS_HUMAN|\[\[done\]\]|\[\[(?:STEP_COMPLETE|STEP_FAILED|PAUSE)\]\])\s*🦾?/gi;

/** Strip orchestration markers from streaming speech text. */
export function sanitizeStreamText(text: string): string {
    return text.replace(ORCHESTRATION_RE, '');
}

type WireEvent = ThinkingEvent & {
    message_id?: string;
    activity_type?: string;
    content?: {
        steps?: ThinkingEvent['plan'];
        goal?: string;
        gather_brief?: string;
        tools?: string[];
        shortlist_size?: number;
        selection_path?: string;
    };
    replace?: boolean;
    run_id?: string;
    thread_id?: string;
    response?: string;
};

/**
 * Normalize a wire event from Python into a ThinkingEvent the reducer understands.
 * Accepts legacy types (execution_plan, turn_start) and AG-UI-shaped types.
 */
export function normalizeTurnEvent(raw: unknown): ThinkingEvent {
    const wire = raw as WireEvent;
    const base: ThinkingEvent = {
        ...(wire as ThinkingEvent),
        timestamp: wire.timestamp ?? Date.now(),
    };

    switch (wire.type as string) {
        case 'activity_snapshot':
            if (wire.activity_type === 'PLAN') {
                return {
                    type: 'execution_plan',
                    id: wire.id,
                    sequence: wire.sequence,
                    timestamp: base.timestamp,
                    plan_id: wire.message_id ?? wire.plan_id,
                    goal: wire.content?.goal ?? wire.goal,
                    plan: wire.content?.steps ?? wire.plan,
                    gather_brief: wire.content?.gather_brief ?? wire.gather_brief,
                };
            }
            if (wire.activity_type === 'TOOLS' && wire.content?.tools) {
                return {
                    type: 'tool_menu',
                    id: wire.id,
                    sequence: wire.sequence,
                    timestamp: base.timestamp,
                    tools: wire.content.tools as string[],
                    shortlist_size: wire.content.shortlist_size as number | undefined,
                    selection_path: wire.content.selection_path as string | undefined,
                };
            }
            return base;

        case 'run_started':
            return {
                type: 'turn_start',
                id: wire.id,
                sequence: wire.sequence,
                timestamp: base.timestamp,
            };

        case 'run_finished':
            return {
                type: 'turn_complete',
                id: wire.id,
                sequence: wire.sequence,
                timestamp: base.timestamp,
                text: wire.response,
            };

        case 'execution_plan':
            return {
                ...base,
                type: 'execution_plan',
            };

        case 'turn_start':
        case 'turn_complete':
            return base;

        default:
            if (wire.text) {
                        return { ...base, text: sanitizeStreamText(wire.text) };
            }
            return base;
    }
}
