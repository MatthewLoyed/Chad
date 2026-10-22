// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./brainSocket', () => ({ brainSocket: { connect: vi.fn() } }));
vi.mock('./commandClient', () => ({ executeCommand: vi.fn() }));

const originalLocation = window.location;
function navigate(href: string) {
    Object.defineProperty(window, 'location', { configurable: true, value: new URL(href) });
}

beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    window.localStorage.clear();
    delete window.pywebview;
});
afterEach(() => {
    vi.useRealTimers();
    delete window.pywebview;
    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
});

describe('desktop transport identity before native injection', () => {
    it.each(['file:///C:/Chad/index.html', 'http://localhost:5173/?chadDesktop=1'])(
        'keeps %s native when bridge injection takes longer than 300 ms', async href => {
            navigate(href);
            const { initBrowserTransport } = await import('./index');
            const { isBrowserTransport, installBrowserTransport } = await import('./pywebviewShim');
            await initBrowserTransport();
            await vi.advanceTimersByTimeAsync(5000);
            expect(isBrowserTransport()).toBe(false);
            expect(window.pywebview).toBeUndefined();
            const native = { api: { get_setup_status: vi.fn() } };
            window.pywebview = native;
            window.dispatchEvent(new Event('pywebviewready'));
            installBrowserTransport();
            expect(window.pywebview).toBe(native);
            expect(isBrowserTransport()).toBe(false);
        },
    );

    it('still initializes a real browser after the native detection window', async () => {
        navigate('http://localhost:5173/');
        const { initBrowserTransport } = await import('./index');
        const ready = initBrowserTransport();
        await vi.advanceTimersByTimeAsync(300);
        await ready;
        const { isBrowserTransport } = await import('./pywebviewShim');
        expect(isBrowserTransport()).toBe(true);
        expect(window.pywebview?.api?.get_conversations).toBeTypeOf('function');
    });

    it('preserves an already injected native bridge', async () => {
        navigate('http://localhost:5173/');
        const native = { api: { get_setup_status: vi.fn() } };
        window.pywebview = native;
        const { initBrowserTransport } = await import('./index');
        await initBrowserTransport();
        expect(window.pywebview).toBe(native);
        const { isBrowserTransport } = await import('./pywebviewShim');
        expect(isBrowserTransport()).toBe(false);
    });
});
