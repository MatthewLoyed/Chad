import { describe, expect, it } from 'vitest';
import { normalizeTurnEvent, sanitizeStreamText } from '../protocol/turnProtocol';

describe('normalizeTurnEvent', () => {
    it('maps activity_snapshot PLAN to execution_plan', () => {
        const normalized = normalizeTurnEvent({
            type: 'activity_snapshot',
            activity_type: 'PLAN',
            message_id: 'turn_3_123',
            content: {
                steps: [{ id: 's1', description: 'Step', status: 'pending' }],
                goal: 'Do work',
            },
            id: 'wire-1',
            sequence: 2,
        });

        expect(normalized.type).toBe('execution_plan');
        expect(normalized.plan_id).toBe('turn_3_123');
        expect(normalized.goal).toBe('Do work');
        expect(normalized.plan).toHaveLength(1);
        expect(normalized.id).toBe('wire-1');
    });

    it('maps run_started to turn_start', () => {
        expect(normalizeTurnEvent({ type: 'run_started', run_id: 'turn_1' }).type).toBe('turn_start');
    });

    it('maps run_finished to turn_complete', () => {
        const evt = normalizeTurnEvent({ type: 'run_finished', run_id: 'turn_1', response: 'Done' });
        expect(evt.type).toBe('turn_complete');
        expect(evt.text).toBe('Done');
    });
});

describe('sanitizeStreamText', () => {
    it('removes orchestration markers', () => {
        const raw = 'Hello TASK_COMPLETED 🦾 world';
        expect(sanitizeStreamText(raw)).toBe('Hello  world');
    });
    it('preserves leading and trailing whitespace', () => {
        expect(sanitizeStreamText('  hello  ')).toBe('  hello  ');
    });
});
