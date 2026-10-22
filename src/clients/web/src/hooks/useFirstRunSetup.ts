import { useEffect, useState } from 'react';

// The desktop bridge can exist before the Brain finishes its cold start.
export function useFirstRunSetup(apiReady: boolean, browserTransport: boolean) {
    const [needsSetup, setNeedsSetup] = useState(false);
    useEffect(() => {
        if (browserTransport || !apiReady) return;
        let cancelled = false;
        let retry: ReturnType<typeof setTimeout> | undefined;
        const check = async () => {
            try {
                const result = await window.pywebview?.api?.get_setup_status?.();
                if (cancelled) return;
                if (result && typeof result === 'object' &&
                    'needs_setup' in result && typeof result.needs_setup === 'boolean') {
                    setNeedsSetup(result.needs_setup);
                    return;
                }
            } catch {
                // A failed bridge request during startup is not a completed check.
            }
            if (!cancelled) retry = setTimeout(check, 3000);
        };
        void check();
        return () => {
            cancelled = true;
            clearTimeout(retry);
        };
    }, [apiReady, browserTransport]);
    return [needsSetup, setNeedsSetup] as const;
}
