/**
 * Tier-2 real-DOM replay harness (typed path).
 * ============================================
 *
 * Mounts the REAL <ChatStream/> in jsdom and drives it through the production
 * typed-stream seam: bridge the recorded frames to StreamEvents, push them through
 * `window.applyStreamEvent` (the sink the Python emitter feeds) → the streamReducer
 * → the `streamTurn` store slice → AIResponseRenderer/ThinkingWidget render.
 *
 * Measurements are read from the rendered DOM exactly as the user sees them, so
 * flicker (length regressions) and truncation (lost thinking tokens) are
 * observable — the verification that stands in for a live browser.
 */
import { render, act, cleanup } from '@testing-library/react';
import ChatStream from '../components/layout/ChatStream';
import { installStreamHandlers } from '../runtime/streamRuntime';
import { useAppStore } from '../store';
import { hasTypedStreamFrames, type RecordedFrame } from './wireFrames';
import type { StreamEvent } from '../protocol/streamEvents';
import { recordedFramesToStreamEvents } from './legacyFrameBridge';

/** Assert a numeric series never decreases (anti-flicker). Returns the first dip. */
export function isMonotonicNonDecreasing(series: number[]): { ok: boolean; at: number } {
    for (let i = 1; i < series.length; i++) {
        if (series[i] < series[i - 1]) return { ok: false, at: i };
    }
    return { ok: true, at: -1 };
}

/** jsdom lacks these browser APIs that ChatStream/framer-motion touch. */
function installJsdomShims(): void {
    if (!Element.prototype.scrollIntoView) {
        Element.prototype.scrollIntoView = function scrollIntoView() { /* no-op */ };
    }
    if (typeof window.matchMedia !== 'function') {
        window.matchMedia = ((query: string) => ({
            matches: false,
            media: query,
            onchange: null,
            addEventListener: () => {},
            removeEventListener: () => {},
            addListener: () => {},
            removeListener: () => {},
            dispatchEvent: () => false,
        })) as unknown as typeof window.matchMedia;
    }
    if (typeof window.ResizeObserver !== 'function') {
        window.ResizeObserver = class {
            observe() {}
            unobserve() {}
            disconnect() {}
        } as unknown as typeof window.ResizeObserver;
    }
}

/**
 * The conversation the replay pretends to be looking at.
 *
 * Rendering of the live turn is gated by `ownsActiveConversation(streamConversationId,
 * activeConversationId)` (session isolation, #269), which returns false when EITHER id
 * is null. A replay that leaves both null therefore mounts ChatStream, folds every
 * event into `streamTurn` correctly — and renders nothing, because the store considers
 * the turn to belong to no visible chat. The content assertions then read '' and only
 * the anti-flicker ones pass, vacuously. Stamping a concrete owner that matches the
 * active chat is what makes the harness observe anything at all.
 */
export const REPLAY_CONVERSATION_ID = 'testkit-replay-conversation';

/** Reset the global store to a clean turn so replays are independent. */
function resetStore(): void {
    // clearThinkingEvents() nulls streamConversationId, so stamp the ids AFTER it.
    useAppStore.getState().clearThinkingEvents();
    useAppStore.setState({
        chatMessages: [],
        tokenUsage: null,
        status: 'thinking',
        streamTurn: null,
        activeConversationId: REPLAY_CONVERSATION_ID,
        streamConversationId: REPLAY_CONVERSATION_ID,
    });
}

/**
 * The thinking text the user sees: reasoning committed into thinking bubbles.
 * Deliberately excludes the "Thinking... (1.2s)" timer label, whose churn is not flicker.
 */
export function readThinkingText(container: HTMLElement): string {
    return Array.from(container.querySelectorAll('.thinking-bubble-wrapper'))
        .map((n) => n.textContent ?? '')
        .join('');
}

/**
 * The AI answer text rendered across all AI bubbles. We scan all of them (not
 * just the last) because under jsdom framer-motion's AnimatePresence exit never
 * completes, so the active block can linger after finalize. In a real browser the
 * exit completes; here we just assert the answer rendered somewhere.
 */
export function readFinalAnswer(container: HTMLElement): string {
    return Array.from(container.querySelectorAll('.chat-message--ai'))
        .map((n) => n.textContent ?? '')
        .join('\n');
}

export interface DomReplaySnapshot {
    /** Rendered thinking-text length after each event (anti-flicker series). */
    thinkingLengthSeries: number[];
    /** Final rendered thinking text as the user would read it. */
    finalThinkingText: string;
    /** Final rendered AI answer text. */
    finalAnswer: string;
    /** Number of plan cards rendered (should be exactly 1 when a plan exists). */
    planCardCount: number;
    /** Live container for ad-hoc assertions. */
    container: HTMLElement;
}

/**
 * Mount real <ChatStream/>, bridge frames to StreamEvents, and push them one at a
 * time through window.applyStreamEvent (worst-case fragmentation — every reducer
 * step is a re-render), snapshotting the rendered DOM after each.
 */
export function replayTurnInDomTyped(frames: RecordedFrame[]): DomReplaySnapshot {
    installJsdomShims();
    cleanup();
    resetStore();

    const utils = render(<ChatStream />);
    const uninstall = installStreamHandlers();
    // A recording captured from the typed seam already IS the event stream production
    // used — replay it verbatim. Only fall back to bridging the legacy `thinking`
    // channel for older, hand-authored fixtures, where the events must be reconstructed.
    const events: StreamEvent[] = hasTypedStreamFrames(frames)
        ? frames.flatMap((f) => (f.ch === 'stream' ? (f.events as StreamEvent[]) : []))
        : recordedFramesToStreamEvents(frames);

    const thinkingLengthSeries: number[] = [];
    for (const event of events) {
        act(() => {
            window.applyStreamEvent?.([event]);
        });
        thinkingLengthSeries.push(readThinkingText(utils.container).length);
    }

    const snapshot: DomReplaySnapshot = {
        thinkingLengthSeries,
        finalThinkingText: readThinkingText(utils.container),
        finalAnswer: readFinalAnswer(utils.container),
        planCardCount: utils.container.querySelectorAll('.plan-card').length,
        container: utils.container,
    };
    uninstall();
    return snapshot;
}

export { cleanup };
