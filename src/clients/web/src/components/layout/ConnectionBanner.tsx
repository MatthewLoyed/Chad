import { useEffect, useState } from 'react';
import { CloudOff, Loader2, RefreshCw } from 'lucide-react';
import { brainSocket, type ConnectionStatus } from '../../transport/brainSocket';
import { isBrowserTransport } from '../../transport/pywebviewShim';
import './ConnectionBanner.css';

/**
 * Phone/browser connection state, shown as a banner above the chat.
 *
 * Chad's Brain runs on the PC; the browser (and the installed PWA) is only a screen for
 * it. When the PC is asleep, off, or off the tailnet, the socket simply stops answering —
 * and because the service worker precaches the app shell, the PWA still opens and renders
 * a full, normal-looking UI against a dead connection. Without this banner the only clue
 * is placeholder text inside the composer, which is easy to miss on a phone and reads as
 * "Chad is broken" rather than "your PC isn't reachable".
 *
 * Desktop (pywebview) is excluded: it can't be in this state, since the Limb and the Brain
 * start together.
 */

const RECONNECTING: ConnectionStatus[] = ['reconnecting', 'error'];

/**
 * How long a non-connected state must persist before it's worth telling the user about.
 *
 * Reconnecting is now routine, not exceptional: iOS drops the socket every time the PWA is
 * backgrounded, so returning to the app ALWAYS reconnects. A banner that appears on every
 * resume trains you to ignore it, and makes a ~300ms round trip feel like an outage. Since
 * a dropped socket no longer costs anything — the turn keeps running on the Brain and is
 * persisted regardless (limb_coordinator.unregister_websocket) — a brief reconnect is not
 * information the user needs.
 *
 * Past this threshold something is genuinely wrong (PC asleep, off the tailnet, Brain down)
 * and silence would be worse than noise.
 */
const NOTIFY_AFTER_MS = 3000;

export function ConnectionBanner() {
    const [status, setStatus] = useState<ConnectionStatus | null>(null);
    // Gates the banner on "not connected for a while" rather than "not connected". Set by a
    // timer armed whenever the socket leaves `connected`, and cleared the moment it returns —
    // so a fast reconnect renders nothing at all.
    const [showBanner, setShowBanner] = useState(false);

    useEffect(() => {
        if (!isBrowserTransport()) return;
        let timer: number | null = null;
        const clear = () => {
            if (timer !== null) {
                window.clearTimeout(timer);
                timer = null;
            }
        };
        const unsub = brainSocket.onStatus((s) => {
            setStatus(s);
            if (s === 'connected' || s === 'unauthorized') {
                clear();
                setShowBanner(false);
                return;
            }
            // Already counting down — don't restart the clock on every retry, or a socket
            // flapping faster than the threshold would stay invisible forever.
            if (timer === null) {
                timer = window.setTimeout(() => {
                    timer = null;
                    setShowBanner(true);
                }, NOTIFY_AFTER_MS);
            }
        });
        return () => {
            clear();
            unsub();
        };
    }, []);

    if (!isBrowserTransport()) return null;
    // 'unauthorized' is deliberately not handled here — App swaps in the login screen for
    // it, and a banner on top of that would just be noise.
    if (status === null || status === 'connected' || status === 'unauthorized') return null;
    if (!showBanner) return null;

    const offline = RECONNECTING.includes(status);

    return (
        <div
            className={`connection-banner ${offline ? 'connection-banner--offline' : 'connection-banner--pending'}`}
            role="status"
            aria-live="polite"
        >
            <span className="connection-banner__icon">
                {offline ? <CloudOff size={16} /> : <Loader2 size={16} className="connection-banner__spin" />}
            </span>
            <span className="connection-banner__text">
                {offline ? (
                    <>
                        <strong>Can&apos;t reach Chad on your PC.</strong>{' '}
                        <span className="connection-banner__hint">
                            Check that it&apos;s awake and Chad is running.
                        </span>
                    </>
                ) : (
                    'Connecting to your PC…'
                )}
            </span>
            {offline && (
                <button
                    type="button"
                    className="connection-banner__retry"
                    onClick={() => brainSocket.reconnect()}
                >
                    <RefreshCw size={13} />
                    Retry
                </button>
            )}
        </div>
    );
}

export default ConnectionBanner;
