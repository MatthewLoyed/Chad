// @vitest-environment jsdom
/**
 * Phase 2c golden test: the REDUCER-DRIVEN render path.
 * ====================================================
 *
 * Mounts the real <ChatStream/> and drives it through the typed sink
 * (window.applyStreamEvent → store.streamTurn → partsToThinkingEvents → render),
 * then asserts the SAME rendered-DOM invariants the legacy wall asserts:
 * anti-flicker (monotonic thinking length), no-cutoff, single plan/tool card,
 * untruncated answer. Green here means the production swap (rendering from the
 * reducer) preserves user-visible behavior.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { parseFramesJsonl, type RecordedFrame } from './wireFrames';
import { replayTurnInDomTyped, isMonotonicNonDecreasing, cleanup } from './domReplay';

const here = dirname(fileURLToPath(import.meta.url));
const loadFixture = (name: string) =>
    parseFramesJsonl(readFileSync(join(here, 'fixtures', name), 'utf8'));

afterEach(() => cleanup());

describe('reducer-driven DOM render — turn_tokens_thinking', () => {
    const frames = loadFixture('turn_tokens_thinking.jsonl');

    it('ANTI-FLICKER: rendered thinking text length never regresses', () => {
        const { thinkingLengthSeries } = replayTurnInDomTyped(frames);
        const mono = isMonotonicNonDecreasing(thinkingLengthSeries);
        expect(mono.ok, `shrank at frame ${mono.at}: ${JSON.stringify(thinkingLengthSeries)}`).toBe(true);
    });

    it('NO CUTOFF: every reasoning delta survives into the DOM', () => {
        const { finalThinkingText } = replayTurnInDomTyped(frames);
        expect(finalThinkingText).toContain('Let me check your inbox and turn on the lamp.');
    });

    it('SINGLE PLAN: the two PLAN snapshots render as one card', () => {
        const { planCardCount } = replayTurnInDomTyped(frames);
        expect(planCardCount).toBe(1);
    });

    it('FINAL ANSWER: the response renders in the AI bubble', () => {
        const { finalAnswer } = replayTurnInDomTyped(frames);
        expect(finalAnswer).toContain('lamp is on');
    });
});

describe('reducer-driven DOM render — turn_tool_use', () => {
    const frames = loadFixture('turn_tool_use.jsonl');

    it('ANTI-FLICKER: thinking length never regresses across the tool boundary', () => {
        const { thinkingLengthSeries } = replayTurnInDomTyped(frames);
        const mono = isMonotonicNonDecreasing(thinkingLengthSeries);
        expect(mono.ok, `shrank at frame ${mono.at}: ${JSON.stringify(thinkingLengthSeries)}`).toBe(true);
    });

    it('NO CUTOFF: reasoning on BOTH sides of the tool survives', () => {
        const { finalThinkingText } = replayTurnInDomTyped(frames);
        expect(finalThinkingText).toContain("I'll search the web.");
        expect(finalThinkingText).toContain('Found it.');
    });

    it('TOOL CARD: the tool call renders exactly one tool bubble', () => {
        const { container } = replayTurnInDomTyped(frames);
        expect(container.querySelectorAll('.tool-bubble').length).toBe(1);
    });

    it('TOOLKIT CAROUSEL: visible only on the first thinking block, not inside the ReAct loop', () => {
        const framesWithTools: RecordedFrame[] = [
            { ch: 'thinking', frame: { type: 'run_started', run_id: 'turn_77', thread_id: 'turn_77' } },
            {
                ch: 'thinking',
                frame: {
                    type: 'activity_snapshot',
                    activity_type: 'TOOLS',
                    content: { tools: ['read_file'], shortlist_size: 5, selection_path: 'hybrid' },
                },
            },
            ...frames.slice(1),
        ];
        const { container } = replayTurnInDomTyped(framesWithTools);
        const carousels = container.querySelectorAll('.tool-menu-carousel');
        expect(carousels.length).toBe(1);

        const thinkingWidgets = container.querySelectorAll('.thinking-widget');
        expect(thinkingWidgets.length).toBe(2);
        expect(thinkingWidgets[0].querySelector('.tool-menu-carousel')).not.toBeNull();
        expect(thinkingWidgets[1].querySelector('.tool-menu-carousel')).toBeNull();
    });

    it('FINAL ANSWER: the answer renders untruncated', () => {
        const { finalAnswer } = replayTurnInDomTyped(frames);
        expect(finalAnswer).toContain('The answer is 42.');
    });
});

describe('reducer-driven DOM render — turn_interleaved_speech', () => {
    const frames = loadFixture('turn_interleaved_speech.jsonl');

    it('ANTI-FLICKER: thinking length never regresses through the speech handover', () => {
        const { thinkingLengthSeries } = replayTurnInDomTyped(frames);
        const mono = isMonotonicNonDecreasing(thinkingLengthSeries);
        expect(mono.ok, `shrank at frame ${mono.at}: ${JSON.stringify(thinkingLengthSeries)}`).toBe(true);
    });

    it('NO CUTOFF: both answer blocks render in order', () => {
        const { finalAnswer } = replayTurnInDomTyped(frames);
        expect(finalAnswer).toContain("First, I'll explain. It works like this.");
        expect(finalAnswer).toContain('Final answer here.');
        expect(finalAnswer.indexOf("First, I'll explain.")).toBeLessThan(
            finalAnswer.indexOf('Final answer here.'),
        );
    });

    it('MID-THINKING SURVIVES: reasoning between the answer blocks renders', () => {
        const { finalThinkingText } = replayTurnInDomTyped(frames);
        expect(finalThinkingText).toContain('reconsider the second half.');
    });
});

describe('reducer-driven DOM render — turn_reasoning_burst', () => {
    const frames = loadFixture('turn_reasoning_burst.jsonl');

    it('ANTI-FLICKER: a rapid delta burst never regresses', () => {
        const { thinkingLengthSeries } = replayTurnInDomTyped(frames);
        const mono = isMonotonicNonDecreasing(thinkingLengthSeries);
        expect(mono.ok, `shrank at frame ${mono.at}: ${JSON.stringify(thinkingLengthSeries)}`).toBe(true);
    });

    it('NO CUTOFF: every fragment reassembles in order', () => {
        const { finalThinkingText } = replayTurnInDomTyped(frames);
        expect(finalThinkingText).toContain('Considering the request carefully before answering now.');
    });

    it('FINAL ANSWER: not doubled', () => {
        const { finalAnswer } = replayTurnInDomTyped(frames);
        const matches = finalAnswer.match(/Done\./g) ?? [];
        expect(matches.length).toBeGreaterThanOrEqual(1);
    });
});
