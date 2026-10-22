import { useState, useCallback } from 'react';
import { getServerUrl } from '../transport/transportConfig';

export type PushState = 'unsupported' | 'blocked' | 'prompt' | 'subscribed' | 'error';

function apiUrl(path: string): string {
    const base = getServerUrl().replace(/\/$/, '');
    return `${base}${path}`;
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export function usePushSubscription() {
    const [state, setState] = useState<PushState>(() => {
        if (!('PushManager' in window) || !('Notification' in window)) return 'unsupported';
        if (Notification.permission === 'denied') return 'blocked';
        if (Notification.permission === 'granted') return 'subscribed';
        return 'prompt';
    });

    const subscribe = useCallback(async (): Promise<boolean> => {
        try {
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') {
                setState('blocked');
                return false;
            }

            const reg = await navigator.serviceWorker.ready;

            // Fetch VAPID public key from the Brain
            const keyRes = await fetch(apiUrl('/api/push/vapid-key'));
            if (!keyRes.ok) throw new Error('Failed to fetch VAPID key');
            const { public_key: publicKey } = await keyRes.json() as { public_key: string };

            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(publicKey) as unknown as ArrayBuffer,
            });

            const postRes = await fetch(apiUrl('/api/push/subscribe'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sub.toJSON()),
            });
            if (!postRes.ok) throw new Error('Failed to register subscription');

            setState('subscribed');
            return true;
        } catch (err) {
            console.error('[PushSubscription] subscribe failed:', err);
            setState('error');
            return false;
        }
    }, []);

    const unsubscribe = useCallback(async (): Promise<void> => {
        try {
            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.getSubscription();
            if (sub) {
                await fetch(apiUrl('/api/push/unsubscribe'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ endpoint: sub.endpoint }),
                });
                await sub.unsubscribe();
            }
            setState('prompt');
        } catch (err) {
            console.error('[PushSubscription] unsubscribe failed:', err);
        }
    }, []);

    return { state, subscribe, unsubscribe };
}
