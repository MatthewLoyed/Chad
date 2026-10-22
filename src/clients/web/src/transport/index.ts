/**
 * Browser transport entry (#188).
 * ===============================
 *
 * One call to wire a browser-hosted Chad to the Brain: seed config from URL query
 * params, then install the `window.pywebview` shim (no-op inside the desktop GUI).
 * Import for side-effect-free helpers, or call `initBrowserTransport()` once at
 * startup (see main.tsx).
 */
import { ingestQueryParams } from './transportConfig';
import { installBrowserTransport, isDesktopPage } from './pywebviewShim';

/**
 * pywebview injects `window.pywebview` asynchronously (it fires a `pywebviewready`
 * event once the native bridge is wired up) — it is not guaranteed to exist yet on
 * the very first tick of our module script, even inside the desktop GUI. Checking
 * `window.pywebview` synchronously can lose that race and misclassify the desktop
 * GUI as a browser, permanently latching the browser-connect modal on. Wait briefly
 * for the real signal before deciding; a real browser will simply never get it and
 * falls through to the timeout.
 */
function waitForPywebview(timeoutMs: number): Promise<void> {
    return new Promise((resolve) => {
        if (window.pywebview) { resolve(); return; }
        const onReady = () => { clearTimeout(timer); resolve(); };
        window.addEventListener('pywebviewready', onReady, { once: true });
        const timer = window.setTimeout(() => {
            window.removeEventListener('pywebviewready', onReady);
            resolve();
        }, timeoutMs);
    });
}

export async function initBrowserTransport(): Promise<void> {
    // Native injection can take seconds on a cold Windows install. React already
    // polls for that bridge; never latch a marked desktop page into browser mode.
    if (isDesktopPage()) return;
    ingestQueryParams();
    await waitForPywebview(300);
    installBrowserTransport();
}

export { brainSocket, type ConnectionStatus } from './brainSocket';
export { isBrowserTransport } from './pywebviewShim';
export * from './transportConfig';
