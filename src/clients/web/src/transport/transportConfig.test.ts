// @vitest-environment jsdom
// Needs a real `window.location` + localStorage; the suite defaults to the node env.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

/**
 * `defaultServerUrl` is only reachable through `getServerUrl()`, and it reads
 * `window.location` at call time — so each case stubs a location and re-imports the
 * module with a cleared localStorage (a stored URL always wins over the default).
 */
async function serverUrlFor(href: string): Promise<string> {
    window.localStorage.clear();
    const url = new URL(href);
    Object.defineProperty(window, 'location', {
        configurable: true,
        value: {
            href,
            origin: url.origin,
            protocol: url.protocol,
            hostname: url.hostname,
            port: url.port,
        },
    });
    vi.resetModules();
    const { getServerUrl } = await import('./transportConfig');
    return getServerUrl();
}

const realLocation = window.location;

describe('defaultServerUrl', () => {
    beforeEach(() => {
        window.localStorage.clear();
    });

    afterEach(() => {
        Object.defineProperty(window, 'location', {
            configurable: true,
            value: realLocation,
        });
        window.localStorage.clear();
    });

    it('appends the Brain port when served by the Vite dev server', async () => {
        expect(await serverUrlFor('http://100.104.1.4:5173/')).toBe('http://100.104.1.4:8767');
    });

    it('appends the Brain port when served by the Brain on a non-default port', async () => {
        expect(await serverUrlFor('http://100.104.1.4:8767/')).toBe('http://100.104.1.4:8767');
    });

    it('uses the origin as-is behind a reverse proxy on 443 (tailscale serve)', async () => {
        // The regression this guards: appending :8767 here yields a URL nothing listens on,
        // because Serve terminates on 443 and forwards to the Brain's real port (8788 in dev).
        // A phone would show a prefilled-but-wrong URL and silently fail to connect.
        expect(await serverUrlFor('https://chad.tailed0bed.ts.net/')).toBe(
            'https://chad.tailed0bed.ts.net',
        );
    });

    it('uses the origin as-is behind a plain-HTTP proxy on port 80', async () => {
        expect(await serverUrlFor('http://chad.example.com/')).toBe('http://chad.example.com');
    });

    it('prefers an explicitly stored URL over the computed default', async () => {
        const href = 'https://chad.tailed0bed.ts.net/';
        const url = new URL(href);
        Object.defineProperty(window, 'location', {
            configurable: true,
            value: {
                href,
                origin: url.origin,
                protocol: url.protocol,
                hostname: url.hostname,
                port: url.port,
            },
        });
        vi.resetModules();
        const { getServerUrl, setServerUrl } = await import('./transportConfig');
        setServerUrl('http://192.168.1.50:8767');
        expect(getServerUrl()).toBe('http://192.168.1.50:8767');
    });
});
