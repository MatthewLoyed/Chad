// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';

/**
 * The banner must stay quiet through a routine reconnect and speak up for a real outage.
 *
 * iOS drops the socket every time the PWA is backgrounded, so coming back to the app ALWAYS
 * reconnects. A banner on every resume trains the user to ignore it and makes a fast round
 * trip feel like an outage — and a dropped socket no longer costs anything, since the turn
 * keeps running on the Brain either way.
 */

const listeners = new Set<(s: string) => void>();

vi.mock('./../../transport/brainSocket', () => ({
    brainSocket: {
        onStatus: (cb: (s: string) => void) => {
            listeners.add(cb);
            return () => listeners.delete(cb);
        },
        reconnect: vi.fn(),
    },
}));

vi.mock('./../../transport/pywebviewShim', () => ({
    isBrowserTransport: () => true,
}));

function emit(status: string) {
    act(() => {
        for (const cb of listeners) cb(status);
    });
}

async function loadBanner() {
    const mod = await import('./ConnectionBanner');
    return mod.ConnectionBanner;
}

describe('ConnectionBanner grace period', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        listeners.clear();
    });

    afterEach(() => {
        // Explicit: this suite doesn't run with `globals: true`, so testing-library's
        // automatic cleanup never registers and renders would accumulate across tests.
        cleanup();
        vi.useRealTimers();
        vi.resetModules();
    });

    it('shows nothing during a reconnect that resolves quickly', async () => {
        const ConnectionBanner = await loadBanner();
        render(<ConnectionBanner />);

        emit('reconnecting');
        act(() => { vi.advanceTimersByTime(800); });
        expect(screen.queryByRole('status')).toBeNull();

        emit('connected');
        act(() => { vi.advanceTimersByTime(10_000); });
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('surfaces a genuine outage once it has lasted past the threshold', async () => {
        const ConnectionBanner = await loadBanner();
        render(<ConnectionBanner />);

        emit('reconnecting');
        act(() => { vi.advanceTimersByTime(3200); });

        const banner = screen.getByRole('status');
        expect(banner.textContent).toContain("Can't reach Chad on your PC");
    });

    it('does not restart the countdown on every retry', async () => {
        // A socket flapping faster than the threshold would otherwise keep resetting the
        // timer and stay invisible forever, which is the failure mode this guards.
        const ConnectionBanner = await loadBanner();
        render(<ConnectionBanner />);

        emit('reconnecting');
        act(() => { vi.advanceTimersByTime(1500); });
        emit('connecting');
        act(() => { vi.advanceTimersByTime(1000); });
        emit('reconnecting');
        act(() => { vi.advanceTimersByTime(700); });

        expect(screen.getByRole('status')).toBeTruthy();
    });

    it('stays silent for unauthorized (the login screen owns that state)', async () => {
        const ConnectionBanner = await loadBanner();
        render(<ConnectionBanner />);

        emit('unauthorized');
        act(() => { vi.advanceTimersByTime(10_000); });
        expect(screen.queryByRole('status')).toBeNull();
    });
});
