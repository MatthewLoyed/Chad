/**
 * Pressing Stop on the PWA must actually CLOSE the turn, not just quiet it.
 *
 * `store.abortActiveTurn` deliberately leaves `streamTurn` alone — it expects the normal
 * end-of-turn path to arrive and mark lifecycle='finished' (which commits the partial
 * bubble). On the desktop that frame is synthesized by the Limb in Python
 * (`gui_limb.abort_active_turn` -> `StreamEventEmitter.run_cancelled`).
 *
 * The browser has no Limb, and the Brain sends nothing terminal for a cancelled turn —
 * only a raw `{type:'status_update'}` frame the web client has no handler for. So the
 * turn sat at lifecycle='running' forever: the thinking stream stopped, but the stop
 * button and overall state kept looking live. brainSocket IS the Limb on this transport,
 * so it owes the same frame.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { brainSocket } from './transport/brainSocket';
import { StreamEventEmitter } from './transport/streamEmitter';
import { applyStreamEvent, initialTurnState, answerText } from './protocol/streamReducer';
import type { StreamEvent } from './protocol/streamEvents';
import type { TurnState } from './protocol/streamReducer';
import { useAppStore } from './store';

/** Capture what brainSocket pushes through window.applyStreamEvent.
 *
 * brainSocket reaches the UI only through `window.applyStreamEvent` /
 * `window.handleStateUpdate`, so a two-property stand-in is enough — no need to pull
 * in jsdom (this stays in the fast node lane, like the other store/transport tests).
 */
function captureEmitted(): StreamEvent[] {
    const seen: StreamEvent[] = [];
    (globalThis as unknown as { window: Record<string, unknown> }).window = {
        applyStreamEvent: (events: StreamEvent[]) => { seen.push(...events); },
        handleStateUpdate: () => { /* status is asserted via the store instead */ },
    };
    return seen;
}

/** Give brainSocket a live turn, as if one were mid-stream. */
function startTurn(emitter: StreamEventEmitter): void {
    const sock = brainSocket as unknown as {
        emitter: StreamEventEmitter | null;
        speechBuffer: string;
    };
    sock.emitter = emitter;
    sock.speechBuffer = '';
}

function reduce(events: StreamEvent[], from?: TurnState): TurnState {
    return events.reduce((s, e) => applyStreamEvent(s, e), from ?? initialTurnState());
}

beforeEach(() => {
    useAppStore.setState({
        streamTurn: null,
        activeTurn: null,
        streamingResponse: null,
        status: 'ready',
        statusText: '',
    });
});

describe('stop button closes the turn on the browser transport', () => {
    it('emits run_finished(user_cancelled) so the turn stops rendering as live', () => {
        const emitter = new StreamEventEmitter('turn-a');
        const seen = captureEmitted();
        startTurn(emitter);

        brainSocket.abortActiveTurn();

        const finished = seen.filter((e) => e.type === 'run_finished');
        expect(finished, 'stop must emit a terminal run_finished frame').toHaveLength(1);
        expect((finished[0] as { stop_reason?: string }).stop_reason).toBe('user_cancelled');
    });

    it('drives the reducer to lifecycle=finished', () => {
        const emitter = new StreamEventEmitter('turn-a');
        const seen = captureEmitted();
        // A turn that has already streamed some of its answer.
        const opening = emitter.runStarted('turn-a');
        const streamed = emitter.textDelta('partial answer so far');
        startTurn(emitter);

        brainSocket.abortActiveTurn();

        const state = reduce([...opening, ...streamed, ...seen]);
        expect(state.lifecycle).toBe('finished');
        // The partial answer the user already saw must survive the cancel — run_cancelled
        // passes response=None precisely so it can't be overwritten with an empty string.
        expect(answerText(state)).toContain('partial answer so far');
    });

    it('flushes a half-spoken sentence into the bubble before closing', () => {
        const emitter = new StreamEventEmitter('turn-a');
        const seen = captureEmitted();
        startTurn(emitter);
        (brainSocket as unknown as { speechBuffer: string }).speechBuffer = 'half a sentence';

        brainSocket.abortActiveTurn();

        // speechFinalize emits the buffered text as a text part (text_delta + text_end)
        // rather than an event of its own — what matters is that it lands BEFORE the
        // terminal frame, so the committed bubble keeps what the user already saw.
        const types = seen.map((e) => e.type);
        expect(types.indexOf('text_delta')).toBeGreaterThanOrEqual(0);
        expect(types.indexOf('text_delta')).toBeLessThan(types.indexOf('run_finished'));
        expect(answerText(reduce(seen))).toContain('half a sentence');
    });

    it('leaves the UI idle and drops the emitter', () => {
        startTurn(new StreamEventEmitter('turn-a'));
        captureEmitted();

        brainSocket.abortActiveTurn();

        expect(useAppStore.getState().status).toBe('ready');
        expect((brainSocket as unknown as { emitter: unknown }).emitter).toBeNull();
    });

    it('is safe when no turn is in flight', () => {
        const seen = captureEmitted();
        (brainSocket as unknown as { emitter: unknown }).emitter = null;

        expect(() => brainSocket.abortActiveTurn()).not.toThrow();
        expect(seen.filter((e) => e.type === 'run_finished')).toHaveLength(0);
    });
});
