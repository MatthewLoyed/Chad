import { useState, useEffect } from 'react';
import { Bell, X } from 'lucide-react';
import { usePushSubscription } from '../../hooks/usePushSubscription';
import './PushBanner.css';

const DISMISSED_KEY = 'chad_push_banner_dismissed';

export default function PushBanner() {
    const { state, subscribe } = usePushSubscription();
    const [visible, setVisible] = useState(false);
    const [subscribing, setSubscribing] = useState(false);

    useEffect(() => {
        // Only show on PWA/browser (not pywebview desktop)
        if (window.pywebview) return;
        if (state !== 'prompt') return;
        if (localStorage.getItem(DISMISSED_KEY)) return;
        // Small delay so the banner doesn't pop up during first-paint
        const t = setTimeout(() => setVisible(true), 2000);
        return () => clearTimeout(t);
    }, [state]);

    if (!visible) return null;

    const handleEnable = async () => {
        setSubscribing(true);
        await subscribe();
        setSubscribing(false);
        setVisible(false);
    };

    const handleDismiss = () => {
        localStorage.setItem(DISMISSED_KEY, '1');
        setVisible(false);
    };

    return (
        <div className="push-banner" role="alert">
            <Bell size={16} className="push-banner__icon" />
            <span className="push-banner__text">
                Enable push notifications so Chad can alert you when tasks complete or need approval.
            </span>
            <button
                className="push-banner__enable"
                onClick={handleEnable}
                disabled={subscribing}
            >
                {subscribing ? 'Enabling…' : 'Enable'}
            </button>
            <button className="push-banner__dismiss" onClick={handleDismiss} aria-label="Dismiss">
                <X size={14} />
            </button>
        </div>
    );
}
