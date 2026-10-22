// @vitest-environment jsdom
/**
 * Phase 2 wiring test: the typed-stream sink.
 * ===========================================
 *
 * Proves the production path window.applyStreamEvent → store.applyStreamEvents →
 * streamTurn folds identically to the pure reducer, including when events arrive
 * in arbitrary batches (the real Python emitter pushes one batch per Brain frame,
 * so a turn lands as many small calls). If batched accumulation ever diverged from
 * a single fold, streaming would tear — this locks that down.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseFramesJsonl } from '../testkit/wireFrames';
import { recordedFramesToStreamEvents } from '../testkit/legacyFrameBridge';
import { reduceStreamEvents } from '../protocol/streamReducer';
import type { StreamEvent } from '../protocol/streamEvents';
import { installStreamHandlers } from './streamRuntime';
import { useAppStore } from '../store';

const here = dirname(fileURLToPath(import.meta.url));
const loadEvents = (name: string): StreamEvent[] =>
    recordedFramesToStreamEvents(
        parseFramesJsonl(readFileSync(join(here, '..', 'testkit', 'fixtures', name), 'utf8')),
    );

const FIXTURES = [
    'turn_tokens_thinking.jsonl',
    'turn_tool_use.jsonl',
    'turn_interleaved_speech.jsonl',
    'turn_reasoning_burst.jsonl',
];

function resetStore(): void {
    useAppStore.getState().clearThinkingEvents();
    useAppStore.setState({ streamTurn: null });
}

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    resetStore();
    delete window.applyStreamEvent;
    vi.useRealTimers();
});

describe('Phase 2 typed-stream sink (window.applyStreamEvent → streamTurn)', () => {
    for (const fixture of FIXTURES) {
        describe(fixture, () => {
            it('single batch folds to the pure reducer result', () => {
                resetStore();
                const uninstall = installStreamHandlers();
                const events = loadEvents(fixture);

                window.applyStreamEvent!(events);

                expect(useAppStore.getState().streamTurn).toEqual(reduceStreamEvents(events));
                uninstall();
            });

            it('arbitrary batches accumulate to the same result', () => {
                resetStore();
                const uninstall = installStreamHandlers();
                const events = loadEvents(fixture);

                // Push events one-at-a-time (worst-case fragmentation).
                for (const e of events) window.applyStreamEvent!([e]);

                expect(useAppStore.getState().streamTurn).toEqual(reduceStreamEvents(events));
                uninstall();
            });

            it('uninstall removes the sink', () => {
                const uninstall = installStreamHandlers();
                expect(typeof window.applyStreamEvent).toBe('function');
                uninstall();
                expect(window.applyStreamEvent).toBeUndefined();
            });
        });
    }

    it('empty batch is a no-op (does not create a turn)', () => {
        resetStore();
        const uninstall = installStreamHandlers();
        window.applyStreamEvent!([]);
        expect(useAppStore.getState().streamTurn).toBeNull();
        uninstall();
    });
});
