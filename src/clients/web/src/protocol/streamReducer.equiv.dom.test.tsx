/**
 * Reducer content/structure proof.
 * ================================
 *
 * Drives every golden fixture through the legacy-frame bridge into the
 * `applyStreamEvent` reducer and asserts the reduced TurnState carries the right
 * thinking/answer text, tool/plan structure, plan replace-semantics, and
 * append-only monotonicity. (The reducer-driven DOM render is verified separately
 * in testkit/turnDomReplayTyped.dom.test.tsx.)
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFramesJsonl } from '../testkit/wireFrames';
import { recordedFramesToStreamEvents } from '../testkit/legacyFrameBridge';
import {
    reduceStreamEvents,
    reasoningText,
    answerText,
    toolParts,
    planParts,
} from './streamReducer';

const here = dirname(fileURLToPath(import.meta.url));
const fixturesDir = join(here, '..', 'testkit', 'fixtures');
const loadFixture = (name: string) =>
    parseFramesJsonl(readFileSync(join(fixturesDir, name), 'utf8'));

/** Collapse runs of whitespace so caption/markdown spacing differences don't matter. */
const norm = (s: string) => s.replace(/\s+/g, ' ').trim();

interface Expectation {
    fixture: string;
    thinkingPhrases: string[];
    answerPhrases: string[];
    toolCount: number;
    planCount: number;
}

const CASES: Expectation[] = [
    {
        fixture: 'turn_tokens_thinking.jsonl',
        thinkingPhrases: ['Let me check your inbox and turn on the lamp.'],
        answerPhrases: ['lamp is on'],
        toolCount: 0,
        planCount: 1,
    },
    {
        fixture: 'turn_tool_use.jsonl',
        thinkingPhrases: ["I'll search the web.", 'Found it.'],
        answerPhrases: ['The answer is 42.'],
        toolCount: 1,
        planCount: 0,
    },
    {
        fixture: 'turn_interleaved_speech.jsonl',
        thinkingPhrases: ['reconsider the second half.'],
        answerPhrases: ["First, I'll explain. It works like this.", 'Final answer here.'],
        toolCount: 0,
        planCount: 0,
    },
    {
        fixture: 'turn_reasoning_burst.jsonl',
        thinkingPhrases: ['Considering the request carefully before answering now.'],
        answerPhrases: ['Done.'],
        toolCount: 0,
        planCount: 0,
    },
];

describe('Phase 1 reducer ⇄ legacy seam equivalence', () => {
    for (const c of CASES) {
        describe(c.fixture, () => {
            const frames = loadFixture(c.fixture);
            const events = recordedFramesToStreamEvents(frames);
            const state = reduceStreamEvents(events);

            it('CONTENT: reducer thinking text contains every reasoning phrase', () => {
                const t = norm(reasoningText(state));
                for (const phrase of c.thinkingPhrases) {
                    expect(t).toContain(norm(phrase));
                }
            });

            it('CONTENT: reducer answer text contains every answer phrase', () => {
                const a = norm(answerText(state));
                for (const phrase of c.answerPhrases) {
                    expect(a).toContain(norm(phrase));
                }
            });

            it('STRUCTURE: tool/plan part counts match the fixture', () => {
                expect(toolParts(state).length).toBe(c.toolCount);
                // Replace semantics: successive PLAN snapshots collapse to one card,
                // matching the legacy "SINGLE PLAN" DOM invariant.
                expect(planParts(state).length).toBe(c.planCount);
            });

            if (c.planCount === 1) {
                it('REPLACE: the single plan reflects the LAST snapshot, not the first', () => {
                    const [plan] = planParts(state);
                    expect(plan).toBeDefined();
                    // turn_tokens_thinking's 2nd snapshot marks s1 completed; replace
                    // semantics means the rendered plan shows the latest status.
                    const s1 = plan.steps.find((s) => s.id === 's1');
                    expect(s1?.status).toBe('completed');
                });
            }

            it('ORDER: reducer parts are append-only (text never shrinks across the fold)', () => {
                // Re-fold incrementally and assert reasoning+answer length is monotonic.
                let prevLen = -1;
                let acc = reduceStreamEvents([]);
                for (const e of events) {
                    acc = reduceStreamEvents(events.slice(0, events.indexOf(e) + 1));
                    const len = (reasoningText(acc) + answerText(acc)).length;
                    // run_started resets — only assert monotonicity after the reset.
                    if (e.type !== 'run_started') {
                        expect(len).toBeGreaterThanOrEqual(prevLen);
                    }
                    prevLen = e.type === 'run_started' ? 0 : len;
                }
            });
        });
    }
});
