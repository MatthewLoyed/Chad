/**
 * Full-screen login gate shown in browser-transport mode when there is no
 * active session.
 *
 * Two modes:
 *  - Supabase configured (VITE_SUPABASE_URL set): Google OAuth + email flows.
 *  - Supabase not configured (self-hosted default): simple server URL + token form.
 */
import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { supabase, isSupabaseConfigured } from '../../services/supabaseClient';
import { getServerUrl, setServerUrl, setToken } from '../../transport/transportConfig';
import { brainSocket } from '../../transport/brainSocket';

type Mode = 'choose' | 'email';
type EmailStep = 'input' | 'magic_sent' | 'password';

export default function LoginScreen({ onConnect }: { onConnect?: () => void }) {
    const [mode, setMode] = useState<Mode>('choose');
    const [emailStep, setEmailStep] = useState<EmailStep>('input');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const clearError = () => setError('');

    // ── Self-hosted token mode (no Supabase) ──────────────────────────────────
    const [serverUrl, setServerUrlLocal] = useState(getServerUrl());
    const [serverToken, setServerToken] = useState('');

    if (!isSupabaseConfigured()) {
        const handleTokenConnect = () => {
            if (!serverToken.trim()) { setError('Enter your server token.'); return; }
            setServerUrl(serverUrl.trim());
            setToken(serverToken.trim());
            brainSocket.reconnect();
            onConnect?.();
        };
        return (
            <div style={styles.overlay}>
                <div style={styles.card}>
                    <div style={styles.header}>
                        <svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
                            <circle cx="16" cy="16" r="16" fill="var(--chad-accent-active, #7c5cff)" opacity="0.15" />
                            <circle cx="16" cy="16" r="9" fill="var(--chad-accent-active, #7c5cff)" />
                        </svg>
                        <h1 style={styles.title}>Connect to Chad</h1>
                    </div>
                    <div>
                        <p style={{ margin: '0 0 16px', fontSize: 13, color: 'rgba(255,255,255,0.55)', lineHeight: 1.5 }}>
                            Enter your Brain server URL and the <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 5px', borderRadius: 4 }}>CHAD_SERVER_TOKEN</code> from your <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 5px', borderRadius: 4 }}>.env</code> file.
                        </p>
                        <div style={{ marginBottom: 10 }}>
                            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', marginBottom: 4 }}>Brain server URL</div>
                            <input
                                style={styles.input}
                                type="url"
                                value={serverUrl}
                                onChange={e => { setServerUrlLocal(e.target.value); clearError(); }}
                                placeholder="http://192.168.1.x:8767"
                                autoCapitalize="off"
                                autoCorrect="off"
                                spellCheck={false}
                            />
                        </div>
                        <div>
                            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', marginBottom: 4 }}>Server token</div>
                            <input
                                style={styles.input}
                                type="password"
                                value={serverToken}
                                onChange={e => { setServerToken(e.target.value); clearError(); }}
                                placeholder="your-secret-token"
                                autoComplete="current-password"
                                onKeyDown={e => e.key === 'Enter' && handleTokenConnect()}
                                autoFocus
                            />
                        </div>
                    </div>
                    {error && <p style={styles.errorText}>{error}</p>}
                    <button style={styles.primaryBtn} onClick={handleTokenConnect} disabled={!serverToken.trim()}>
                        Connect
                    </button>
                    <p style={styles.footer}>Token is stored locally in your browser — never sent anywhere except your own Brain.</p>
                </div>
            </div>
        );
    }

    // ── Google OAuth ──────────────────────────────────────────────────────────
    const handleGoogle = async () => {
        setBusy(true);
        clearError();
        const { error: e } = await supabase.auth.signInWithOAuth({
            provider: 'google',
            options: { redirectTo: window.location.origin },
        });
        if (e) { setError(e.message); setBusy(false); }
        // On success the page navigates away → no setBusy(false) needed
    };

    // ── Magic link ────────────────────────────────────────────────────────────
    const handleMagicLink = async () => {
        if (!email.trim()) { setError('Enter your email address.'); return; }
        setBusy(true);
        clearError();
        const { error: e } = await supabase.auth.signInWithOtp({
            email: email.trim(),
            options: { emailRedirectTo: window.location.origin },
        });
        setBusy(false);
        if (e) { setError(e.message); return; }
        setEmailStep('magic_sent');
    };

    // ── Email + password ──────────────────────────────────────────────────────
    const handlePassword = async () => {
        if (!email.trim() || !password) { setError('Enter email and password.'); return; }
        setBusy(true);
        clearError();
        const { error: e } = await supabase.auth.signInWithPassword({
            email: email.trim(),
            password,
        });
        setBusy(false);
        if (e) setError(e.message);
        // On success useAuth picks up the session via onAuthStateChange
    };

    return (
        <div style={styles.overlay}>
            <div style={styles.card}>
                {/* ── Header ─────────────────────────────────────────── */}
                <div style={styles.header}>
                    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
                        <circle cx="16" cy="16" r="16" fill="var(--chad-accent-active, #7c5cff)" opacity="0.15" />
                        <circle cx="16" cy="16" r="9" fill="var(--chad-accent-active, #7c5cff)" />
                    </svg>
                    <h1 style={styles.title}>Sign in to Chad</h1>
                </div>

                {/* ── Choose method ───────────────────────────────────── */}
                {mode === 'choose' && (
                    <>
                        <button style={styles.googleBtn} onClick={handleGoogle} disabled={busy}>
                            {busy ? (
                                <Loader2 size={18} className="animate-spin" />
                            ) : (
                                <GoogleIcon />
                            )}
                            <span>Continue with Google</span>
                        </button>

                        <div style={styles.divider}>
                            <span style={styles.dividerLine} />
                            <span style={styles.dividerText}>or</span>
                            <span style={styles.dividerLine} />
                        </div>

                        <button
                            style={styles.secondaryBtn}
                            onClick={() => { setMode('email'); clearError(); }}
                        >
                            Continue with email
                        </button>
                    </>
                )}

                {/* ── Email flow ──────────────────────────────────────── */}
                {mode === 'email' && emailStep === 'input' && (
                    <>
                        <input
                            style={styles.input}
                            type="email"
                            placeholder="you@example.com"
                            value={email}
                            onChange={e => { setEmail(e.target.value); clearError(); }}
                            autoFocus
                            autoComplete="email"
                        />
                        <button style={styles.primaryBtn} onClick={handleMagicLink} disabled={busy || !email.trim()}>
                            {busy ? <Loader2 size={16} className="animate-spin" /> : 'Send magic link'}
                        </button>

                        <button
                            style={{ ...styles.ghostBtn, marginTop: 2 }}
                            onClick={() => setEmailStep('password')}
                        >
                            Use password instead
                        </button>
                        <button style={styles.ghostBtn} onClick={() => { setMode('choose'); clearError(); }}>
                            ← Back
                        </button>
                    </>
                )}

                {mode === 'email' && emailStep === 'password' && (
                    <>
                        <input
                            style={styles.input}
                            type="email"
                            placeholder="Email"
                            value={email}
                            onChange={e => { setEmail(e.target.value); clearError(); }}
                            autoComplete="email"
                        />
                        <input
                            style={styles.input}
                            type="password"
                            placeholder="Password"
                            value={password}
                            onChange={e => { setPassword(e.target.value); clearError(); }}
                            autoFocus
                            autoComplete="current-password"
                        />
                        <button style={styles.primaryBtn} onClick={handlePassword} disabled={busy || !email.trim() || !password}>
                            {busy ? <Loader2 size={16} className="animate-spin" /> : 'Sign in'}
                        </button>
                        <button style={styles.ghostBtn} onClick={() => setEmailStep('input')}>
                            ← Use magic link
                        </button>
                    </>
                )}

                {mode === 'email' && emailStep === 'magic_sent' && (
                    <div style={styles.sentBox}>
                        <p style={styles.sentText}>
                            Check your inbox — we sent a link to <strong>{email}</strong>.
                            Click it to sign in.
                        </p>
                        <button style={styles.ghostBtn} onClick={() => { setEmailStep('input'); clearError(); }}>
                            Resend or use a different email
                        </button>
                    </div>
                )}

                {error && <p style={styles.errorText}>{error}</p>}

                <p style={styles.footer}>
                    Your data stays in your Brain — we only use Supabase for identity.
                </p>
            </div>
        </div>
    );
}

function GoogleIcon() {
    return (
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z"/>
            <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"/>
            <path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"/>
            <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"/>
        </svg>
    );
}

const styles: Record<string, React.CSSProperties> = {
    overlay: {
        position: 'fixed',
        inset: 0,
        background: '#0c0d10',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 3000,
    },
    card: {
        width: 'min(400px, 92vw)',
        background: '#16181d',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 14,
        padding: '32px 28px',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        color: '#e5e7eb',
    },
    header: {
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        marginBottom: 4,
    },
    title: {
        margin: 0,
        fontSize: 20,
        fontWeight: 700,
        color: '#f1f3f5',
    },
    googleBtn: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        padding: '10px 16px',
        fontSize: 14,
        fontWeight: 600,
        borderRadius: 8,
        border: '1px solid rgba(255,255,255,0.18)',
        background: '#fff',
        color: '#1f2937',
        cursor: 'pointer',
        transition: 'opacity 0.15s',
    },
    primaryBtn: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        padding: '10px 16px',
        fontSize: 14,
        fontWeight: 600,
        borderRadius: 8,
        border: 'none',
        background: 'var(--chad-accent-active, #7c5cff)',
        color: '#fff',
        cursor: 'pointer',
    },
    secondaryBtn: {
        padding: '10px 16px',
        fontSize: 14,
        fontWeight: 500,
        borderRadius: 8,
        border: '1px solid rgba(255,255,255,0.14)',
        background: 'transparent',
        color: '#e5e7eb',
        cursor: 'pointer',
    },
    ghostBtn: {
        background: 'none',
        border: 'none',
        color: 'rgba(255,255,255,0.45)',
        fontSize: 13,
        cursor: 'pointer',
        padding: '4px 0',
        textAlign: 'center' as const,
    },
    input: {
        padding: '9px 11px',
        fontSize: 14,
        background: '#0f1115',
        color: '#e5e7eb',
        border: '1px solid rgba(255,255,255,0.12)',
        borderRadius: 8,
        outline: 'none',
        width: '100%',
        boxSizing: 'border-box' as const,
    },
    divider: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        margin: '2px 0',
    },
    dividerLine: {
        flex: 1,
        height: 1,
        background: 'rgba(255,255,255,0.08)',
        display: 'block',
    },
    dividerText: {
        fontSize: 12,
        color: 'rgba(255,255,255,0.35)',
    },
    errorText: {
        margin: 0,
        fontSize: 13,
        color: '#f87171',
    },
    sentBox: {
        display: 'flex',
        flexDirection: 'column' as const,
        gap: 10,
    },
    sentText: {
        margin: 0,
        fontSize: 14,
        color: 'rgba(255,255,255,0.7)',
        lineHeight: 1.5,
    },
    footer: {
        margin: '4px 0 0',
        fontSize: 11,
        color: 'rgba(255,255,255,0.25)',
        textAlign: 'center' as const,
    },
};
