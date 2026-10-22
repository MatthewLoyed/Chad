import { useEffect, useState } from 'react';
import type { Session, User } from '@supabase/supabase-js';
import { supabase, isSupabaseConfigured } from '../services/supabaseClient';
import { setToken, setServerUrl, getServerUrl } from '../transport/transportConfig';
import { brainSocket } from '../transport/brainSocket';
import { isBrowserTransport } from '../transport/pywebviewShim';

export interface AuthState {
    session: Session | null;
    user: User | null;
    loading: boolean;
}

/**
 * Provides Supabase auth state for the browser transport.
 * In desktop (pywebview) mode this hook is a no-op — auth is handled by
 * CHAD_SERVER_TOKEN on the desktop side.
 *
 * On session change the hook updates the stored Brain token with the fresh
 * Supabase JWT and reconnects the WebSocket so the Brain sees a valid credential.
 */
export function useAuth(): AuthState {
    const [session, setSession] = useState<Session | null>(null);
    const [user, setUser] = useState<User | null>(null);
    // There is nothing to await when auth can't run here (desktop transport, or Supabase
    // unconfigured), so start settled instead of flipping `loading` inside the effect —
    // that flip caused a render with a spinner that was never going to resolve.
    const [loading, setLoading] = useState(() => isBrowserTransport() && isSupabaseConfigured());

    useEffect(() => {
        if (!isBrowserTransport() || !isSupabaseConfigured()) {
            return;
        }

        // Seed initial session (handles OAuth redirect callback automatically)
        supabase.auth.getSession().then(({ data }) => {
            const s = data.session;
            setSession(s);
            setUser(s?.user ?? null);
            if (s) _syncToken(s);
            setLoading(false);
        });

        // Keep in sync on refresh / sign-out
        const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, s) => {
            setSession(s);
            setUser(s?.user ?? null);
            if (s) {
                _syncToken(s);
            } else {
                // Signed out — disconnect the Brain socket
                brainSocket.disconnect();
            }
        });

        return () => subscription.unsubscribe();
    }, []);

    return { session, user, loading };
}

/** Push the current access_token into transportConfig and (re)connect. */
function _syncToken(session: Session): void {
    const jwt = session.access_token;
    if (!jwt) return;
    setToken(jwt);
    // Derive the Brain WS URL from the page origin if not already set
    const stored = getServerUrl();
    if (!stored || stored.includes('localhost')) {
        // Keep whatever the user configured; only auto-set if it looks like default
    } else {
        setServerUrl(stored);
    }
    brainSocket.reconnect();
}
