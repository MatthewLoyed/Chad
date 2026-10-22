import { describe, expect, it } from 'vitest';
import { applyTurnEvent, dedupeExecutionPlans, normalizeTurnEvents } from '../reducers/turnEventReducer';
import type { ThinkingEvent } from '../store';

const SAMPLE_STEPS = [
    { id: 's1', description: 'Check email', tools: ['list_emails'], status: 'pending' as const },
    { id: 's2', description: 'Turn on lamp', tools: ['control_device'], status: 'pending' as const },
];

describe('applyTurnEvent — execution_plan', () => {
    it('upserts when plan_id matches', () => {
        let turn = applyTurnEvent(null, {
            type: 'execution_plan',
            plan_id: 'turn_1',
            plan: SAMPLE_STEPS,
            goal: 'Multi-step task',
        }).activeTurn;

        turn = applyTurnEvent(turn, {
            type: 'execution_plan',
            plan_id: 'turn_1',
            plan: [{ ...SAMPLE_STEPS[0], status: 'completed' }, SAMPLE_STEPS[1]],
            goal: 'Multi-step task',
        }).activeTurn;

        const plans = turn!.events.filter((e) => e.type === 'execution_plan');
        expect(plans).toHaveLength(1);
        expect(plans[0].plan?.[0].status).toBe('completed');
    });

    it('keeps a single plan when plan_id values differ (regression)', () => {
        let turn = applyTurnEvent(null, {
            type: 'execution_plan',
            plan_id: 'session-uuid',
            plan: SAMPLE_STEPS,
            goal: 'Task A',
        }).activeTurn;

        turn = applyTurnEvent(turn, {
            type: 'execution_plan',
            plan_id: 'turn_3_1718300000000',
            plan: SAMPLE_STEPS,
            goal: 'Task A',
        }).activeTurn;

        const plans = turn!.events.filter((e) => e.type === 'execution_plan');
        expect(plans).toHaveLength(1);
        expect(plans[0].plan_id).toBe('turn_3_1718300000000');
    });

    it('ignores duplicate delivery by event id', () => {
        const first = applyTurnEvent(null, {
            id: 'evt-1',
            type: 'execution_plan',
            plan_id: 'turn_1',
            plan: SAMPLE_STEPS,
        });

        const second = applyTurnEvent(first.activeTurn, {
            id: 'evt-1',
            type: 'execution_plan',
            plan_id: 'turn_1',
            plan: SAMPLE_STEPS,
        });

        expect(second.skipped).toBe(true);
        expect(second.activeTurn!.events.filter((e) => e.type === 'execution_plan')).toHaveLength(1);
    });
});

describe('normalizeTurnEvents', () => {
    it('collapses duplicate plans in persisted history', () => {
        const events: ThinkingEvent[] = [
            { type: 'execution_plan', plan_id: 'a', plan: SAMPLE_STEPS },
            { type: 'reasoning', text: 'thinking...' },
            { type: 'execution_plan', plan_id: 'b', plan: SAMPLE_STEPS },
        ];
        const normalized = normalizeTurnEvents(events);
        expect(normalized.filter((e) => e.type === 'execution_plan')).toHaveLength(1);
        expect(normalized.find((e) => e.type === 'execution_plan')?.plan_id).toBe('b');
    });
});

describe('dedupeExecutionPlans', () => {
    it('keeps the last plan update', () => {
        const events: ThinkingEvent[] = [
            { type: 'execution_plan', plan_id: 'old', plan: SAMPLE_STEPS },
            { type: 'execution_plan', plan_id: 'new', plan: SAMPLE_STEPS },
        ];
        const out = dedupeExecutionPlans(events);
        expect(out.filter((e) => e.type === 'execution_plan')).toHaveLength(1);
        expect(out[0].plan_id).toBe('new');
    });
});
