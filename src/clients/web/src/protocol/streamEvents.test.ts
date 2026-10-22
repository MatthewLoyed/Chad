import { describe, expect, it } from 'vitest';
import {
    assertNever,
    detectSeqGap,
    type StreamEvent,
} from './streamEvents';

describe('streamEvents — seq gap detection', () => {
    it('first event of a turn is always ok', () => {
        expect(detectSeqGap(null, { turn_id: 't', seq: 7 })).toEqual({ ok: true, missed: 0 });
    });

    it('consecutive seq is ok with no misses', () => {
        expect(detectSeqGap(4, { turn_id: 't', seq: 5 })).toEqual({ ok: true, missed: 0 });
    });

    it('a gap reports the count of missed events', () => {
        expect(detectSeqGap(4, { turn_id: 't', seq: 7 })).toEqual({ ok: false, missed: 2 });
    });

    it('reorder/duplicate reports negative missed', () => {
        expect(detectSeqGap(5, { turn_id: 't', seq: 5 }).missed).toBe(-1);
    });
});

describe('streamEvents — exhaustive handling', () => {
    // A reducer skeleton: the assertNever default makes an unhandled case a COMPILE
    // error, so adding a new event type forces every consumer to handle it.
    const kindOf = (e: StreamEvent): string => {
        switch (e.type) {
            case 'run_started':
            case 'run_finished':
            case 'run_error':
                return 'lifecycle';
            case 'reasoning_delta':
            case 'reasoning_end':
                return 'reasoning';
            case 'text_delta':
            case 'text_end':
                return 'text';
            case 'tool_start':
            case 'tool_end':
                return 'tool';
            case 'plan_snapshot':
                return 'plan';
            case 'tools_snapshot':
                return 'tool_menu';
            case 'token_usage':
                return 'telemetry';
            default:
                return assertNever(e);
        }
    };

    it('classifies a reasoning delta', () => {
        expect(kindOf({ type: 'reasoning_delta', turn_id: 't', seq: 1, part_id: 'r1', delta: 'hi' }))
            .toBe('reasoning');
    });

    it('classifies a tool start', () => {
        expect(kindOf({ type: 'tool_start', turn_id: 't', seq: 2, tool_call_id: 'c1', name: 'web_search' }))
            .toBe('tool');
    });

    it('classifies a plan snapshot', () => {
        expect(kindOf({ type: 'plan_snapshot', turn_id: 't', seq: 3, plan_id: 'p1', steps: [] }))
            .toBe('plan');
    });
});
