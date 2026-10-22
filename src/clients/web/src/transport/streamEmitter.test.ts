import { describe, expect, it } from 'vitest';
import { StreamEventEmitter } from './streamEmitter';
import type { StreamEvent } from '../protocol/streamEvents';

/**
 * These assertions mirror the Python `StreamEventEmitter` contract
 * (src/remote/stream_events.py) so the browser transport decomposes a turn into
 * parts identically to the desktop Limb.
 */
function types(events: StreamEvent[]): string[] {
    return events.map((e) => e.type);
}

describe('StreamEventEmitter — part boundaries (parity with stream_events.py)', () => {
    it('stamps monotonic seq and shared turn_id, resetting on run_started', () => {
        const em = new StreamEventEmitter();
        em.runStarted('turn-A'); // seq 0
        const a = em.reasoningDelta('x'); // seq 1
        const b = em.reasoningDelta('y'); // seq 2
        expect(a[0].turn_id).toBe('turn-A');
        expect(a[0].seq).toBe(1);
        expect(b[0].seq).toBe(2);

        const restart = em.runStarted('turn-B');
        expect(restart[0].seq).toBe(0);
        expect(restart[0].turn_id).toBe('turn-B');
    });

    it('coalesces consecutive reasoning deltas into one part, closed by a non-reasoning event', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const d1 = em.reasoningDelta('a');
        const d2 = em.reasoningDelta('b');
        expect(d1[0]).toMatchObject({ type: 'reasoning_delta', part_id: 'reasoning-0' });
        expect(d2[0]).toMatchObject({ type: 'reasoning_delta', part_id: 'reasoning-0' });

        // A tool_start closes the open reasoning part first.
        const tool = em.toolStart('call-1', 'web_search', { q: 'hi' }, 'Searching…');
        expect(types(tool)).toEqual(['reasoning_end', 'tool_start']);

        // The next reasoning delta opens a fresh part.
        const d3 = em.reasoningDelta('c');
        expect(d3[0]).toMatchObject({ part_id: 'reasoning-1' });
    });

    it('accumulates text deltas into one part; speech_finalize ends it', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        expect(em.textDelta('Hello ')[0]).toMatchObject({ type: 'text_delta', part_id: 'text-0' });
        expect(em.textDelta('world')[0]).toMatchObject({ part_id: 'text-0' });
        expect(types(em.speechFinalize('Hello world'))).toEqual(['text_end']);
        // Next text delta opens a new part.
        expect(em.textDelta('again')[0]).toMatchObject({ part_id: 'text-1' });
    });

    it('speech_finalize with no prior delta synthesizes a text_delta then text_end', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const out = em.speechFinalize('only block');
        expect(types(out)).toEqual(['text_delta', 'text_end']);
        expect(out[0]).toMatchObject({ delta: 'only block' });
    });

    it('run_finished synthesizes a text part from the response when nothing streamed', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const out = em.runFinished('final answer', undefined, 1234);
        expect(types(out)).toEqual(['text_delta', 'text_end', 'run_finished']);
        const finished = out[2] as Extract<StreamEvent, { type: 'run_finished' }>;
        expect(finished.response).toBe('final answer');
        expect(finished.turn_duration_ms).toBe(1234);
    });

    it('run_finished closes a streamed text part instead of synthesizing a new one', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        em.textDelta('streamed');
        const out = em.runFinished('streamed');
        // text part already open -> just text_end + run_finished (no synthesized delta)
        expect(types(out)).toEqual(['text_end', 'run_finished']);
    });

    it('plan_snapshot shares one plan_id per turn and closes open reasoning', () => {
        const em = new StreamEventEmitter('turn-9');
        em.runStarted();
        em.reasoningDelta('thinking');
        const out = em.planSnapshot([{ tool: 'x' } as never], 'do the thing');
        expect(types(out)).toEqual(['reasoning_end', 'plan_snapshot']);
        const snap = out[1] as Extract<StreamEvent, { type: 'plan_snapshot' }>;
        expect(snap.plan_id).toBe('turn-9-plan');
        expect(snap.goal).toBe('do the thing');
    });

    it('empty reasoning/text deltas emit nothing', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        expect(em.reasoningDelta('')).toEqual([]);
        expect(em.textDelta('')).toEqual([]);
    });

    it('tool_end closes open reasoning so the next agent opens a fresh part', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const scout = em.reasoningDelta('scout facts', 'Scout', 'scout_1');
        expect(scout[0]).toMatchObject({ part_id: 'reasoning-0', parent_id: 'scout_1' });

        const end = em.toolEnd('scout_1', true, { gather_brief_ready: true });
        expect(types(end)).toEqual(['reasoning_end', 'tool_end']);

        const planner = em.reasoningDelta('now plan');
        expect(planner[0]).toMatchObject({ part_id: 'reasoning-1' });
        expect(planner[0]).not.toHaveProperty('parent_id');
    });

    it('attribution change closes reasoning without waiting for tool_end', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const scout = em.reasoningDelta('scout facts', 'Scout', 'scout_1');
        const handoff = em.reasoningDelta('planner brief');
        expect(types(handoff)).toEqual(['reasoning_end', 'reasoning_delta']);
        expect(scout[0].part_id).not.toBe(
            (handoff[1] as Extract<StreamEvent, { type: 'reasoning_delta' }>).part_id,
        );
        expect(handoff[1]).not.toHaveProperty('parent_id');
    });

    it('same attribution keeps coalescing into one reasoning part', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const a = em.reasoningDelta('a', 'Scout', 'scout_1');
        const b = em.reasoningDelta('b', 'Scout', 'scout_1');
        expect(a[0].part_id).toBe(b[0].part_id);
        expect(types(b)).toEqual(['reasoning_delta']);
    });

    it('suppresses research worker thoughts without closing active orchestrator reasoning', () => {
        const em = new StreamEventEmitter('t');
        em.runStarted();
        const orch1 = em.reasoningDelta('Orchestrator plan', 'Deep Research', 'dep_1');
        expect(types(orch1)).toEqual(['reasoning_delta']);

        // Worker thought arrives interleaved
        const worker = em.reasoningDelta('Worker scratchpad', 'Research Worker 1', 'dep_1');
        expect(worker).toEqual([]);

        // Next orchestrator chunk continues same reasoning part without interruption
        const orch2 = em.reasoningDelta(' continuing plan', 'Deep Research', 'dep_1');
        expect(types(orch2)).toEqual(['reasoning_delta']);
        expect(orch2[0].part_id).toBe(orch1[0].part_id);
    });
});
