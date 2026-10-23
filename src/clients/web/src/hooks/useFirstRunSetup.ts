import { useEffect, useState } from 'react';
import { setupRequest } from '../components/setup/setupRequest';

const STARTUP_TIMEOUT_MS = 90000;
const STARTUP_RETRY_MS = 3000;

// null means readiness is still unknown: do not render Chat/disconnection UI yet.
export function useFirstRunSetup(apiReady: boolean, browserTransport: boolean) {
    const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
    const [startupError, setStartupError] = useState(false);
    const [attempt, setAttempt] = useState(0);
    useEffect(() => {
        if (browserTransport) {
            setNeedsSetup(false);
            return;
        }
        let cancelled = false;
        let retry: ReturnType<typeof setTimeout> | undefined;
        const deadline = setTimeout(() => {
            cancelled = true;
            clearTimeout(retry);
            setStartupError(true);
        }, STARTUP_TIMEOUT_MS);
        const check = async () => {
            if (!apiReady) return;
            try {
                const api = window.pywebview?.api;
                if (!api?.get_setup_status) throw new Error('Desktop connection not ready');
                const result = await setupRequest(api.get_setup_status());
                if (cancelled) return;
                if (result && typeof result === 'object' &&
                    'needs_setup' in result && typeof result.needs_setup === 'boolean') {
                    clearTimeout(deadline);
                    setStartupError(false);
                    setNeedsSetup(result.needs_setup);
                    return;
                }
            } catch {
                // Startup failures are retried, then shown as a recoverable startup error.
            }
            if (!cancelled) retry = setTimeout(check, STARTUP_RETRY_MS);
        };
        void check();
        return () => {
            cancelled = true;
            clearTimeout(deadline);
            clearTimeout(retry);
        };
    }, [apiReady, browserTransport, attempt]);
    const retryStartup = () => {
        setStartupError(false);
        setNeedsSetup(null);
        setAttempt(value => value + 1);
    };
    return [needsSetup, setNeedsSetup, startupError, retryStartup] as const;
}
