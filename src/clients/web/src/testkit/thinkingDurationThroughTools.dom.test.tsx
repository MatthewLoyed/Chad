// @vitest-environment jsdom
/**
 * GUI-symptom: after ~1s the thinking header collapses to "Thought for 1 second"
 * while tools are still running for a long time.
 *
 * Cause: hasContentStarted treated tool_start as "answer started", which stamped
 * thinkingEndTime and flipped isActiveTurn off. Correct: keep the live timer
 * through tools until speech/answer arrives (or the turn finalizes).
 */
import { afterEach, describe, expect, it } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import ChatStream from '../components/layout/ChatStream';
import { useAppStore } from '../store';
import { applyStreamEvent, initialTurnState } from '../protocol/streamReducer';
import type { StreamEvent } from '../protocol/streamEvents';
import { REPLAY_CONVERSATION_ID } from './domReplay';

afterEach(cleanup);

function installShims(): void {
    if (!Element.prototype.scrollIntoView) {
        Element.prototype.scrollIntoView = function () { /* no-op */ };
    }
    if (typeof window.matchMedia !== 'function') {
        window.matchMedia = ((q: string) => ({
            matches: false, media: q, onchange: null,
            addEventListener: () => {}, removeEventListener: () => {},
            addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
        })) as unknown as typeof window.matchMedia;
    }
    if (typeof window.ResizeObserver !== 'function') {
        window.ResizeObserver = class {
            observe() {} unobserve() {} disconnect() {}
        } as unknown as typeof window.ResizeObserver;
    }
}

function fold(events: StreamEvent[]) {
    let turn = initialTurnState();
    for (const e of events) turn = applyStreamEvent(turn, e);
    return turn;
}

describe('thinking duration through tools', () => {
    it('keeps the live thinking timer after tool_start (does not collapse to Thought for 1s)', () => {
        installShims();
        const started = Date.now() - 1_200;
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: [],
            tokenUsage: null,
            status: 'thinking',
            liveStatus: 'Working through it…',
            liveStatusUpdatedAt: Date.now(),
            thinkingStartTime: started,
            thinkingEndTime: null,
            turnStartTime: started,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: REPLAY_CONVERSATION_ID,
            streamTurn: fold([
                { type: 'run_started', turn_id: 't-tools', seq: 0 },
                {
                    type: 'tools_snapshot',
                    turn_id: 't-tools',
                    menu_id: 'menu1',
                    tools: ['web_search'],
                    shortlist_size: 1,
                    selection_path: 'hybrid',
                    seq: 1,
                },
                {
                    type: 'tool_start',
                    turn_id: 't-tools',
                    tool_call_id: 'tool1',
                    name: 'web_search',
                    args: { query: 'x' },
                    display_text: 'Searching…',
                    seq: 2,
                },
            ]),
        });

        const { container } = render(<ChatStream />);

        // Live shimmer/timer — not a completed "Thought for 1 second" collapse.
        expect(container.querySelector('.thinking-label--live')).toBeTruthy();
        expect(container.textContent).not.toMatch(/Thought for 1 seconds?/i);
        expect(useAppStore.getState().thinkingEndTime).toBeNull();
    });

    it('does not stamp thinkingEndTime on tools alone; stamps once speech arrives', () => {
        installShims();
        const started = Date.now() - 12_000;
        useAppStore.getState().clearThinkingEvents();

        const withTools = fold([
            { type: 'run_started', turn_id: 't-speech', seq: 0 },
            {
                type: 'tool_start',
                turn_id: 't-speech',
                tool_call_id: 'tool1',
                name: 'web_search',
                args: { query: 'x' },
                display_text: 'Searching…',
                seq: 1,
            },
            {
                type: 'tool_end',
                turn_id: 't-speech',
                tool_call_id: 'tool1',
                ok: true,
                result: 'ok',
                seq: 2,
            },
        ]);

        useAppStore.setState({
            chatMessages: [],
            status: 'thinking',
            thinkingStartTime: started,
            thinkingEndTime: null,
            turnStartTime: started,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: REPLAY_CONVERSATION_ID,
            streamTurn: withTools,
        });

        const { rerender } = render(<ChatStream />);
        expect(useAppStore.getState().thinkingEndTime).toBeNull();

        useAppStore.setState({
            streamTurn: fold([
                { type: 'run_started', turn_id: 't-speech', seq: 0 },
                {
                    type: 'tool_start',
                    turn_id: 't-speech',
                    tool_call_id: 'tool1',
                    name: 'web_search',
                    args: { query: 'x' },
                    display_text: 'Searching…',
                    seq: 1,
                },
                {
                    type: 'tool_end',
                    turn_id: 't-speech',
                    tool_call_id: 'tool1',
                    ok: true,
                    result: 'ok',
                    seq: 2,
                },
                { type: 'text_delta', turn_id: 't-speech', part_id: 'a1', delta: 'Here is the answer.', seq: 3 },
            ]),
        });
        rerender(<ChatStream />);

        expect(useAppStore.getState().thinkingEndTime).toBeTypeOf('number');
        expect(useAppStore.getState().thinkingEndTime! - started).toBeGreaterThanOrEqual(11_000);
    });

    it('renders "Thought for X seconds" when turn finishes and is the last message in chat', () => {
        installShims();
        useAppStore.setState({
            chatMessages: [
                {
                    id: 'msg-final',
                    role: 'ai',
                    text: 'Here is the final answer.',
                    timestamp: new Date(),
                    thoughts: [{ type: 'thought', text: 'Let me think through this carefully.' }],
                    thinkingDurationMs: 5000,
                    turnDurationMs: 6000,
                },
            ],
            streamTurn: null,
            thinkingStartTime: null,
            thinkingEndTime: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
        });

        const { container } = render(<ChatStream />);
        expect(container.textContent).toContain('Thought for 5 seconds');
    });

    it('renders distinct durations for multiple thinking blocks separated by tools in history without lumping', () => {
        installShims();
        useAppStore.setState({
            chatMessages: [
                {
                    id: 'msg-multi-thought',
                    role: 'ai',
                    text: 'Here is the final response.',
                    timestamp: new Date(),
                    thoughts: [
                        {
                            type: 'thought',
                            text: 'First thought before tool.',
                            durationMs: 15_000,
                            timestamp: 1000,
                        },
                        {
                            type: 'tool_start',
                            name: 'gmail_search',
                            timestamp: 16000,
                        },
                        {
                            type: 'tool_end',
                            name: 'gmail_search',
                            result: 'emails found',
                            timestamp: 18000,
                        },
                        {
                            type: 'thought',
                            text: 'Second thought after tool.',
                            durationMs: 3_000,
                            timestamp: 18000,
                        },
                    ],
                    thinkingDurationMs: 18_000,
                    turnDurationMs: 21_000,
                },
            ],
            streamTurn: null,
            thinkingStartTime: null,
            thinkingEndTime: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
        });

        const { container } = render(<ChatStream />);
        expect(container.textContent).toContain('Thought for 15 seconds');
        expect(container.textContent).toContain('Thought for 3 seconds');
        expect(container.textContent).not.toContain('Thought for 18 seconds');
    });

    it('renders distinct durations for multiple reasoning blocks in a finished streamTurn without lumping', () => {
        installShims();
        const finishedTurn = fold([
            { type: 'run_started', turn_id: 't-stream-multi', seq: 0 },
            { type: 'reasoning_delta', turn_id: 't-stream-multi', part_id: 'r0', delta: 'First live thought', seq: 1 },
            { type: 'reasoning_end', turn_id: 't-stream-multi', part_id: 'r0', seq: 2 },
            {
                type: 'tool_start',
                turn_id: 't-stream-multi',
                tool_call_id: 'tc1',
                name: 'gmail_search',
                args: {},
                display_text: 'Searching mail…',
                seq: 3,
            },
            {
                type: 'tool_end',
                turn_id: 't-stream-multi',
                tool_call_id: 'tc1',
                ok: true,
                result: 'emails',
                seq: 4,
            },
            { type: 'reasoning_delta', turn_id: 't-stream-multi', part_id: 'r1', delta: 'Second live thought', seq: 5 },
            { type: 'reasoning_end', turn_id: 't-stream-multi', part_id: 'r1', seq: 6 },
            { type: 'text_delta', turn_id: 't-stream-multi', part_id: 'a0', delta: 'Final answer.', seq: 7 },
            { type: 'run_finished', turn_id: 't-stream-multi', turn_duration_ms: 21000, seq: 8 },
        ]);

        // Manually assign distinct durationMs on the reasoning parts to simulate realistic timing
        const r0 = finishedTurn.parts.find((p) => p.id === 'r0');
        if (r0 && r0.kind === 'reasoning') {
            r0.durationMs = 15_000;
        }
        const r1 = finishedTurn.parts.find((p) => p.id === 'r1');
        if (r1 && r1.kind === 'reasoning') {
            r1.durationMs = 3_000;
        }

        useAppStore.setState({
            chatMessages: [],
            streamTurn: finishedTurn,
            thinkingStartTime: null,
            thinkingEndTime: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: REPLAY_CONVERSATION_ID,
        });

        const { container } = render(<ChatStream />);
        expect(container.textContent).toContain('Thought for 15 seconds');
        expect(container.textContent).toContain('Thought for 3 seconds');
        expect(container.textContent).not.toContain('Thought for 18 seconds');
    });
});


