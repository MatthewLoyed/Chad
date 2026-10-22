/**
 * Browser connect gate (#188).
 * ============================
 *
 * The "small settings screen" for a browser-hosted Chad: enter the Brain server
 * URL + auth token, persisted to localStorage (transportConfig). Only rendered
 * when the browser transport is installed (i.e. not the desktop GUI). Acts as a
 * blocking gate until credentials are entered, then shows live connection state
 * and lets the user re-open it to edit the connection.
 *
 * The floating status chip is toast-style: visible while connecting / reconnecting
 * / errored, and briefly after a successful connect, then fades away. Idle
 * "Connected" does not stay on screen.
 */
import { useEffect, useRef, useState } from 'react';
import { Plug, Loader2, CheckCircle2, AlertTriangle, LogOut } from 'lucide-react';
import {
    brainSocket,
    getServerUrl,
    setServerUrl,
    isBrowserTransport,
    type ConnectionStatus,
} from '../../transport';
import { supabase } from '../../services/supabaseClient';
import { useAuth } from '../../hooks/useAuth';

const CONNECTED_TOAST_MS = 2000;

const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
};
const card: React.CSSProperties = {
    width: 'min(420px, 92vw)', background: '#16181d',
    border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 24,
    display: 'flex', flexDirection: 'column', gap: 14, color: '#e5e7eb',
};
const label: React.CSSProperties = { fontSize: 12, color: 'rgba(255,255,255,0.55)', marginBottom: 4 };
const input: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', padding: '9px 11px', fontSize: 14,
    background: '#0f1115', color: '#e5e7eb',
    border: '1px solid rgba(255,255,255,0.12)', borderRadius: 8, outline: 'none',
};
const btn: React.CSSProperties = {
    padding: '9px 14px', fontSize: 14, fontWeight: 600, borderRadius: 8,
    border: 'none', cursor: 'pointer',
    background: 'var(--chad-accent-active, #7c5cff)', color: '#fff',
};

function statusLabel(s: ConnectionStatus): { text: string; icon: React.ReactNode } {
    switch (s) {
        case 'connected': return { text: 'Connected', icon: <CheckCircle2 size={14} color="#4ade80" /> };
        case 'connecting':
        case 'authenticating': return { text: 'Connecting…', icon: <Loader2 size={14} className="animate-spin" /> };
        case 'reconnecting': return { text: 'Reconnecting…', icon: <Loader2 size={14} className="animate-spin" /> };
        default: return { text: 'Not connected', icon: <AlertTriangle size={14} color="#f59e0b" /> };
    }
}

function shouldShowChip(status: ConnectionStatus): boolean {
    return status !== 'connected';
}

export default function BrowserConnectModal() {
    const [open, setOpen] = useState(false);
    const [url, setUrl] = useState(getServerUrl());
    const initialStatus: ConnectionStatus = brainSocket.connected ? 'connected' : 'error';
    const [status, setStatus] = useState<ConnectionStatus>(initialStatus);
    // Idle connected → no chip. Connecting / reconnecting / error → show.
    const [chipVisible, setChipVisible] = useState(() => shouldShowChip(initialStatus));
    const [chipFading, setChipFading] = useState(false);
    const hideTimer = useRef<number | null>(null);
    const fadeTimer = useRef<number | null>(null);
    const prevStatus = useRef<ConnectionStatus>(initialStatus);
    const { user } = useAuth();

    useEffect(() => {
        const clearTimers = () => {
            if (hideTimer.current !== null) {
                window.clearTimeout(hideTimer.current);
                hideTimer.current = null;
            }
            if (fadeTimer.current !== null) {
                window.clearTimeout(fadeTimer.current);
                fadeTimer.current = null;
            }
        };

        return brainSocket.onStatus((next) => {
            const prev = prevStatus.current;
            prevStatus.current = next;
            setStatus(next);
            clearTimers();
            setChipFading(false);

            if (shouldShowChip(next)) {
                setChipVisible(true);
                return;
            }

            // Connected after a visible problem/transition → brief toast, then fade.
            // Duplicate connected (e.g. page already healthy) → stay hidden.
            if (shouldShowChip(prev)) {
                setChipVisible(true);
                hideTimer.current = window.setTimeout(() => {
                    setChipFading(true);
                    fadeTimer.current = window.setTimeout(() => {
                        setChipVisible(false);
                        setChipFading(false);
                    }, 320);
                }, CONNECTED_TOAST_MS);
            } else {
                setChipVisible(false);
            }
        });
    }, []);

    useEffect(() => () => {
        if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
        if (fadeTimer.current !== null) window.clearTimeout(fadeTimer.current);
    }, []);

    if (!isBrowserTransport()) return null;

    const handleConnect = () => {
        setServerUrl(url);
        brainSocket.reconnect();
        setOpen(false);
    };

    const handleSignOut = async () => {
        brainSocket.disconnect();
        await supabase.auth.signOut();
    };

    const st = statusLabel(status);

    // Floating status chip — toast-style; tap re-opens the panel while visible.
    if (!open) {
        if (!chipVisible) return null;
        return (
            <button
                type="button"
                onClick={() => setOpen(true)}
                title="Brain connection"
                style={{
                    position: 'fixed', bottom: 12, right: 12, zIndex: 1500,
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '6px 10px', fontSize: 12, borderRadius: 999,
                    background: '#16181d', color: '#e5e7eb',
                    border: '1px solid rgba(255,255,255,0.12)', cursor: 'pointer',
                    opacity: chipFading ? 0 : 1,
                    transition: 'opacity 0.3s ease',
                    pointerEvents: chipFading ? 'none' : 'auto',
                }}
            >
                {st.icon}<span>{st.text}</span>
            </button>
        );
    }

    return (
        <div style={overlay} onClick={() => setOpen(false)}>
            <div style={card} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Plug size={18} />
                    <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Brain Connection</h2>
                </div>

                {/* Signed-in user badge */}
                {user && (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', background: 'rgba(255,255,255,0.04)', borderRadius: 8 }}>
                        <div>
                            <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', marginBottom: 2 }}>Signed in as</div>
                            <div style={{ fontSize: 13, color: '#e5e7eb' }}>{user.email}</div>
                        </div>
                        <button
                            type="button"
                            title="Sign out"
                            onClick={handleSignOut}
                            style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer', padding: 4 }}
                        >
                            <LogOut size={15} />
                        </button>
                    </div>
                )}

                <div>
                    <div style={label}>Brain server URL</div>
                    <input
                        style={input}
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        placeholder="http://localhost:8767"
                        autoCapitalize="off"
                        autoCorrect="off"
                        spellCheck={false}
                    />
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'rgba(255,255,255,0.6)' }}>
                    {st.icon}<span>{st.text}</span>
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                    <button
                        type="button"
                        onClick={() => setOpen(false)}
                        style={{ ...btn, background: 'transparent', color: '#e5e7eb', border: '1px solid rgba(255,255,255,0.15)' }}
                    >
                        Cancel
                    </button>
                    <button type="button" onClick={handleConnect} style={btn}>
                        Connect
                    </button>
                </div>
            </div>
        </div>
    );
}
