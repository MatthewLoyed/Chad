// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { BrainSocket, type ConnectionStatus } from './brainSocket';

/**
 * Two phone-only regressions, both of which looked like "the token doesn't save" or
 * "it stops keeping up" from the outside.
 *
 * These drive the socket's message handler and visibility handler directly rather than
 * standing up a real WebSocket — the behavior under test is entirely in how a pre-auth
 * ERROR is classified, and in what a resume decides to do.
 */

type Sock = BrainSocket & {
    handleMessage: (m: unknown) => Promise<void>;
    hasAuthenticated: boolean;
    authRejected: boolean;
    shouldRun: boolean;
    needsResync: boolean;
    hiddenSince: number | null;
    ws: unknown;
    _onVisibilityChange: () => void;
    reconnect: () => void;
};

function makeSocket(): { sock: Sock; statuses: ConnectionStatus[] } {
    const sock = new BrainSocket() as Sock;
    const statuses: ConnectionStatus[] = [];
    sock.onStatus((s) => statuses.push(s));
    return { sock, statuses };
}

function setVisibility(state: 'visible' | 'hidden') {
    Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        get: () => state,
    });
}

describe('pre-auth ERROR classification', () => {
    it('does NOT reject the credential when the Brain is merely still starting up', async () => {
        // The regression: this path fires AFTER the token was verified server-side, but the
        // client treated any pre-auth ERROR as a bad token — and App deletes the stored
        // token on `unauthorized`. Resuming into a booting Brain silently unpaired the phone.
        const { sock } = makeSocket();
        await sock.handleMessage({
            type: 'error',
            payload: { error: 'Brain is still starting up. Try again in a moment.', code: 'not_ready' },
        });
        expect(sock.authRejected).toBe(false);
    });

    it('rejects the credential when the Brain says the token is invalid', async () => {
        const { sock } = makeSocket();
        await sock.handleMessage({
            type: 'error',
            payload: { error: 'Invalid token', code: 'invalid_token' },
        });
        expect(sock.authRejected).toBe(true);
    });

    it('still rejects on a legacy Brain that sends no code', async () => {
        const { sock } = makeSocket();
        await sock.handleMessage({ type: 'error', payload: { error: 'Invalid token' } });
        expect(sock.authRejected).toBe(true);
    });

    it('treats an unrecognized pre-auth error as transient, not as a bad token', async () => {
        const { sock } = makeSocket();
        await sock.handleMessage({ type: 'error', payload: { error: 'something else went wrong' } });
        expect(sock.authRejected).toBe(false);
    });

    it('ignores the auth classification entirely once authenticated', async () => {
        const { sock } = makeSocket();
        sock.hasAuthenticated = true;
        await sock.handleMessage({ type: 'error', payload: { error: 'Invalid token', code: 'invalid_token' } });
        expect(sock.authRejected).toBe(false);
    });
});

describe('resume from background', () => {
    beforeEach(() => {
        setVisibility('visible');
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('forces a reconnect after a real background, even when readyState still says OPEN', () => {
        // iOS kills the socket on background but often never fires onclose, so readyState
        // reads OPEN on a dead connection. Gating the resume on `!connected` meant the app
        // sat on a zombie socket showing stale content until it was force-quit.
        const { sock } = makeSocket();
        sock.shouldRun = true;
        sock.ws = { readyState: WebSocket.OPEN };
        const reconnect = vi.spyOn(sock, 'reconnect').mockImplementation(() => {});

        setVisibility('hidden');
        sock._onVisibilityChange();
        sock.hiddenSince = Date.now() - 60_000; // a minute in the app switcher
        setVisibility('visible');
        sock._onVisibilityChange();

        expect(reconnect).toHaveBeenCalledTimes(1);
        expect(sock.needsResync).toBe(true);
    });

    it('does not churn the connection on a brief tab flick', () => {
        const { sock } = makeSocket();
        sock.shouldRun = true;
        sock.ws = { readyState: WebSocket.OPEN };
        const reconnect = vi.spyOn(sock, 'reconnect').mockImplementation(() => {});

        setVisibility('hidden');
        sock._onVisibilityChange();
        setVisibility('visible');
        sock._onVisibilityChange(); // effectively 0ms hidden

        expect(reconnect).not.toHaveBeenCalled();
    });

    it('reconnects on resume when the socket is genuinely closed, however brief', () => {
        const { sock } = makeSocket();
        sock.shouldRun = true;
        sock.ws = { readyState: WebSocket.CLOSED };
        const reconnect = vi.spyOn(sock, 'reconnect').mockImplementation(() => {});

        setVisibility('hidden');
        sock._onVisibilityChange();
        setVisibility('visible');
        sock._onVisibilityChange();

        expect(reconnect).toHaveBeenCalledTimes(1);
    });
});

describe('resync on reconnect', () => {
    it('fires resync listeners after a re-auth, but not after the first auth', async () => {
        const { sock } = makeSocket();
        const fired: number[] = [];
        sock.onResync(() => fired.push(1));

        // First successful auth — the app loads its own state normally, no resync wanted.
        await sock.handleMessage({ type: 'auth', payload: { status: 'success' } });
        expect(fired).toHaveLength(0);

        // A drop, then a reconnect: the view may have missed anything that happened while down.
        sock.needsResync = true;
        await sock.handleMessage({ type: 'auth', payload: { status: 'success' } });
        expect(fired).toHaveLength(1);

        // And it must not re-fire on a subsequent clean auth.
        await sock.handleMessage({ type: 'auth', payload: { status: 'success' } });
        expect(fired).toHaveLength(1);
    });
});
