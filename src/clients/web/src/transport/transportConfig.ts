/**
 * Browser transport config (#188).
 * ================================
 *
 * The serverUrl + auth token a browser-hosted Chad uses to reach the Brain.
 * Persisted to localStorage so a phone/browser session survives reloads, and
 * seedable from URL query params (`?serverUrl=...&token=...`) for one-tap setup
 * on a new device. Desktop pywebview never imports this — the shim is only
 * installed when `window.pywebview` is absent.
 */

const SERVER_URL_KEY = 'chad.serverUrl';
const TOKEN_KEY = 'chad.token';

const BRAIN_PORT = 8767;

/**
 * Best-effort default Brain URL: the host the app was served from, on the Brain's
 * port. Covers these deployments without manual entry —
 *   • served by the Brain itself (http://<ip>:8767) → same origin, port 8767
 *   • served by the Vite dev server (http://<ip>:5173) → same host, port 8767
 *   • served through a reverse proxy on 443/80 (`tailscale serve`) → the origin AS IS
 * Falls back to localhost in non-browser/SSR contexts.
 *
 * The reverse-proxy case is why the port isn't appended unconditionally. Behind
 * `tailscale serve` the app is reached at https://<host>.ts.net (port 443) and the proxy
 * forwards to whatever port the Brain actually listens on — often 8788 for dev, not 8767.
 * Appending BRAIN_PORT there produced https://<host>.ts.net:8767, which nothing listens
 * on, so a phone would silently fail to connect with a prefilled-but-wrong URL. An empty
 * `port` means the default for the scheme, which is exactly the "something is proxying for
 * us" signal — in that case the Brain is reachable at the origin itself.
 */
function defaultServerUrl(): string {
    try {
        const { protocol, hostname, port, origin } = window.location;
        if (hostname) {
            if (!port) return origin;
            return `${protocol}//${hostname}:${BRAIN_PORT}`;
        }
    } catch { /* no window — fall through */ }
    return `http://localhost:${BRAIN_PORT}`;
}

function read(key: string): string | null {
    try {
        return window.localStorage.getItem(key);
    } catch {
        return null;
    }
}

function write(key: string, value: string): void {
    try {
        window.localStorage.setItem(key, value);
    } catch {
        /* private mode / storage disabled — config is then in-memory only */
    }
}

/**
 * Seed config from URL query params (if present), persisting them and stripping
 * them from the visible URL so a shared link doesn't leak the token in history.
 */
export function ingestQueryParams(): void {
    const params = new URLSearchParams(window.location.search);
    const url = params.get('serverUrl');
    const token = params.get('token');
    let mutated = false;
    if (url) { write(SERVER_URL_KEY, url.trim()); mutated = true; }
    if (token) { write(TOKEN_KEY, token.trim()); mutated = true; }
    if (mutated) {
        params.delete('serverUrl');
        params.delete('token');
        const qs = params.toString();
        const clean = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash;
        window.history.replaceState(null, '', clean);
    }
}

export function getServerUrl(): string {
    return read(SERVER_URL_KEY) || defaultServerUrl();
}

export function setServerUrl(url: string): void {
    write(SERVER_URL_KEY, url.trim());
}

export function getToken(): string {
    return read(TOKEN_KEY) || '';
}

export function setToken(token: string): void {
    write(TOKEN_KEY, token.trim());
}

export function hasCredentials(): boolean {
    return !!getToken();
}

/** Normalize a configured serverUrl into the REST base (http/https, no /ws, no trailing slash). */
export function toHttpBase(url: string): string {
    let base = url.replace(/^ws:\/\//, 'http://').replace(/^wss:\/\//, 'https://');
    if (base.includes('/ws')) {
        base = base.replace(/\/+$/, '').replace('/ws', '');
    } else {
        base = base.replace(/\/+$/, '');
    }
    return base;
}

/** Normalize a configured serverUrl into the WebSocket endpoint (ws/wss, with /ws). */
export function toWsUrl(url: string): string {
    let ws = url.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://');
    ws = ws.replace(/\/+$/, '');
    if (!/\/ws$/.test(ws)) {
        ws = `${ws}/ws`;
    }
    return ws;
}
