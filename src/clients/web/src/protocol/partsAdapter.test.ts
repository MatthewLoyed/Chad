/**
 * Phase 2 adapter test: partsToThinkingEvents.
 * ============================================
 *
 * The reduced TurnState must reconstruct the flat ThinkingEvent[] the existing
 * renderer consumes, so the reducer can become the single render source without
 * rewriting the render tree. Drives the Phase 0 fixtures through bridge→reduce→
 * adapter and locks the resulting event contract (types, order, text, tool/plan
 * fields).
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFramesJsonl } from '../testkit/wireFrames';
import { recordedFramesToStreamEvents } from '../testkit/legacyFrameBridge';
import { reduceStreamEvents, partsToThinkingEvents } from './streamReducer';
import type { ThinkingEvent } from '../store';

const here = dirname(fileURLToPath(import.meta.url));
const adapt = (name: string): ThinkingEvent[] => {
    const frames = parseFramesJsonl(
        readFileSync(join(here, '..', 'testkit', 'fixtures', name), 'utf8'),
    );
    return partsToThinkingEvents(reduceStreamEvents(recordedFramesToStreamEvents(frames)));
};

const reasoning = (evs: ThinkingEvent[]) =>
    evs.filter((e) => e.type === 'reasoning').map((e) => e.text).join('');
const speech = (evs: ThinkingEvent[]) =>
    evs.filter((e) => e.type === 'speech').map((e) => e.text).join('');

describe('partsToThinkingEvents', () => {
    it('tool_use: reasoning, one resolved tool_start, speech', () => {
        const evs = adapt('turn_tool_use.jsonl');
        expect(reasoning(evs)).toBe("I'll search the web.Found it.");
        expect(speech(evs)).toBe('The answer is 42.');

        const tools = evs.filter((e) => e.type === 'tool_start');
        expect(tools).toHaveLength(1);
        expect(tools[0]).toMatchObject({
            correlation_id: 'tc1',
            name: 'web_search',
            success: true,
            result: '42',
        });
        // No separate tool_end event — the resolved node carries the outcome.
        expect(evs.some((e) => e.type === 'tool_end')).toBe(false);
    });

    it('interleaved_speech: two speech blocks in order, mid-thinking preserved', () => {
        const evs = adapt('turn_interleaved_speech.jsonl');
        const speeches = evs.filter((e) => e.type === 'speech').map((e) => e.text);
        expect(speeches).toEqual([
            "First, I'll explain. It works like this. ",
            'Final answer here.',
        ]);
        expect(reasoning(evs)).toContain('reconsider the second half.');
    });

    it('tokens_thinking: single execution_plan reflecting the LAST snapshot, response-only answer', () => {
        const evs = adapt('turn_tokens_thinking.jsonl');
        const plans = evs.filter((e) => e.type === 'execution_plan');
        expect(plans).toHaveLength(1);
        expect(plans[0].plan?.find((s) => s.id === 's1')?.status).toBe('completed');
        expect(speech(evs)).toContain('lamp is on');
    });

    it('burst: answer is not doubled', () => {
        const evs = adapt('turn_reasoning_burst.jsonl');
        expect(speech(evs)).toBe('Done.');
        expect(reasoning(evs)).toContain('Considering the request carefully before answering now.');
    });

    it('order is preserved: reasoning → tool → reasoning → speech for tool_use', () => {
        const evs = adapt('turn_tool_use.jsonl');
        const types = evs.map((e) => e.type);
        expect(types).toEqual(['reasoning', 'tool_start', 'reasoning', 'speech']);
    });
});
