/**
 * A reconnect must not leave the previous socket delivering into the store.
 *
 * Symptom: the PWA rendered every chunk twice — "The user The user wants wants to to".
 *
 * `open()` assigned `this.ws = ws` without closing whatever was already attached.
 * `reconnect()` closed one explicitly, but `connect()` and the `scheduleReconnect()`
 * timer did not. On iOS that is the normal path: the OS kills the connection without
 * firing onclose, so the dead socket still reads readyState=OPEN (see
 * STALE_AFTER_HIDDEN_MS) and its `onmessage` keeps firing. Two live handlers fed the
 * same store, so every frame was applied twice.
 *
 * It only became visible when the Brain started fanning turn output out to every
 * connected viewer (#711) — before that the superseded socket received nothing.
 *
 * Second bug in the same place: the old socket's `onclose` did `this.ws = null`
 * unconditionally, which would null out the reference to the socket that had just
 * replaced it, and re-enter scheduleReconnect for a connection replaced on purpose.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./transportConfig', () => ({
    getServerUrl: () => 'http://localhost:8788',
    getToken: () => 'test-token',
    toWsUrl: () => 'ws://localhost:8788/ws',
    toHttpBase: (u: string) => u,
}));

import { brainSocket } from './brainSocket';

class FakeSocket {
    static instances: FakeSocket[] = [];
    onopen: (() => void) | null = null;
    onmessage: ((ev: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    readyState = 1; // OPEN
    closed = false;
    sent: string[] = [];

    constructor() {
        FakeSocket.instances.push(this);
    }

    send(payload: string) { this.sent.push(payload); }

    close() {
        this.closed = true;
        this.readyState = 3;
        // Real sockets fire onclose asynchronously after close(); if a handler is still
        // attached this is exactly what used to clobber the replacement socket.
        this.onclose?.();
    }
}

/** brainSocket reaches the UI through window.*; a stand-in keeps this in the node lane. */
function stubWindow(): { applied: unknown[] } {
    const applied: unknown[] = [];
    (globalThis as unknown as { window: Record<string, unknown> }).window = {
        applyStreamEvent: (events: unknown) => { applied.push(events); },
        handleStateUpdate: () => {},
        setTimeout: (fn: () => void, ms: number) => setTimeout(fn, ms),
        clearTimeout: (id: number) => clearTimeout(id),
    };
    return { applied };
}

const sock = () => brainSocket as unknown as {
    open: () => void;
    ws: FakeSocket | null;
    shouldRun: boolean;
    hasAuthenticated: boolean;
};

/** Did anything convince the client it is authenticated on the current socket? */
const liveAuthed = () => sock().hasAuthenticated;

beforeEach(() => {
    FakeSocket.instances = [];
    stubWindow();
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
    sock().ws = null;
    sock().shouldRun = true;
});

describe('reconnect never leaves a second socket attached', () => {
    it('closes the previous socket when opening a new one', () => {
        sock().open();
        const first = FakeSocket.instances[0];

        sock().open();

        expect(FakeSocket.instances).toHaveLength(2);
        expect(first.closed, 'the superseded socket must be closed').toBe(true);
    });

    it('detaches the old handlers so frames are not applied twice', () => {
        sock().open();
        const first = FakeSocket.instances[0];

        sock().open();

        expect(first.onmessage, 'a stale onmessage is what doubled the stream').toBeNull();
        expect(first.onclose).toBeNull();
        expect(first.onopen).toBeNull();
    });

    it('a frame already queued on a superseded socket is not applied', () => {
        sock().open();
        const first = FakeSocket.instances[0];
        // Captured BEFORE the swap — a frame the OS had already queued on the dead socket.
        const staleOnMessage = first.onmessage;
        sock().open();
        const live = sock().ws!;
        expect(liveAuthed()).toBe(false);

        staleOnMessage?.({ data: JSON.stringify({ type: 'auth', payload: { status: 'success' } }) });

        expect(liveAuthed(), 'a dead socket must not drive connection state').toBe(false);
        expect(live.closed).toBe(false);
    });

    it('a superseded socket closing does not clear the live socket', () => {
        sock().open();
        const first = FakeSocket.instances[0];
        // iOS routinely delivers the dead socket's close AFTER the replacement is attached.
        const staleOnClose = first.onclose;
        sock().open();
        const live = sock().ws;

        staleOnClose?.();

        expect(sock().ws, 'a stale close must not null out the live socket').toBe(live);
    });

    it('the newest socket is the one that authenticates', () => {
        sock().open();
        sock().open();
        const live = sock().ws!;

        live.onopen?.();

        expect(live.sent.length, 'the live socket sends AUTH').toBe(1);
        expect(FakeSocket.instances[0].sent).toHaveLength(0);
    });
});
