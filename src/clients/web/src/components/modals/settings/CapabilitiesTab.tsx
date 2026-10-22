import { useCallback, useEffect, useState } from 'react';
import { History, RefreshCw, ShieldCheck } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { SkeletonState, ErrorState } from '../../common/AsyncState';
import { readCache, writeCache } from '../../../utils/localCache';
import './CapabilitiesTab.css';

interface DaemonInfo {
    id: string;
    label: string;
    description: string;
    enabled: boolean;
    running: boolean;
    interval_minutes: number | null;
    interval_unit?: 'minutes' | 'hours';
}

interface AutomationsSummary {
    daemons?: DaemonInfo[];
}

interface CapabilityDef {
    id: string;
    icon: LucideIcon;
    name: string;
    reads: string[];
    consentPrompt: string;
}

/**
 * Built-in, no-credential local capabilities the user opts into individually — distinct
 * from Integrations (third-party connectors that need an account/API key). Everything
 * here defaults off; enabling one reads local personal data (e.g. browser history) even
 * though nothing leaves the machine unless Supabase is separately configured.
 */
const CAPABILITY_CATALOG: CapabilityDef[] = [
    {
        id: 'ingestion_sync',
        icon: History,
        name: 'Browser history sync',
        reads: [
            'Visited URLs, page titles, and YouTube video links from your Chrome profile',
            "Reads directly from Chrome's local history file — no browser extension or account needed",
        ],
        consentPrompt:
            'Enable browser history sync? Chad will read visited URLs, page titles, and YouTube links ' +
            "from your Chrome profile so you can ask about pages you've visited. This stays local unless " +
            'you have Supabase configured. You can turn it off anytime.',
    },
];

export default function CapabilitiesTab() {
    const [daemons, setDaemons] = useState<DaemonInfo[]>(() => {
        const cached = readCache<AutomationsSummary>('automations_summary');
        return cached?.daemons ?? [];
    });
    const [loading, setLoading] = useState(() => !readCache<AutomationsSummary>('automations_summary'));
    const [loadError, setLoadError] = useState<unknown>(null);
    const [saving, setSaving] = useState<string | null>(null);
    const [syncing, setSyncing] = useState<string | null>(null);
    const [syncResult, setSyncResult] = useState<Record<string, { success: boolean; message: string }>>({});

    const refresh = useCallback(async () => {
        const api = window.pywebview?.api;
        // Clear `loading` before bailing — see AutomationsTab: returning first
        // pinned this pane to "Loading capabilities…" permanently.
        if (!api?.get_automations_summary) {
            setLoadError(new Error('Capabilities are unavailable on this client.'));
            setLoading(false);
            return;
        }
        if (!readCache('automations_summary')) {
            setLoading(true);
        }
        setLoadError(null);
        try {
            const summaryRes = await api.get_automations_summary();
            const s = summaryRes as AutomationsSummary;
            setDaemons(s?.daemons ?? []);
            if (s) writeCache('automations_summary', s);
        } catch (err) {
            console.error('Failed to load capabilities', err);
            setLoadError(err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { refresh(); }, [refresh]);

    const toggleCapability = async (capability: CapabilityDef, nextEnabled: boolean) => {
        if (nextEnabled && !window.confirm(capability.consentPrompt)) return;

        const api = window.pywebview?.api;
        if (!api?.update_daemon_settings) return;
        setSaving(capability.id);
        try {
            await api.update_daemon_settings(capability.id, nextEnabled, undefined);
            await refresh();
        } catch (err) {
            console.error('Failed to update capability', err);
        } finally {
            setSaving(null);
        }
    };

    const handleSync = async (capabilityId: string) => {
        const api = window.pywebview?.api;
        if (!api?.trigger_ingestion_sync) {
            setSyncResult((prev) => ({
                ...prev,
                [capabilityId]: { success: false, message: 'Sync not available on this client' },
            }));
            return;
        }
        setSyncing(capabilityId);
        try {
            const res = (await api.trigger_ingestion_sync('chrome')) as {
                success?: boolean;
                added_count?: number;
                message?: string;
                error?: string;
            } | null;
            if (res?.success) {
                const count = res.added_count ?? 0;
                setSyncResult((prev) => ({
                    ...prev,
                    [capabilityId]: {
                        success: true,
                        message: res.message || (count > 0 ? `Synced +${count} new items` : 'Up to date'),
                    },
                }));
            } else {
                setSyncResult((prev) => ({
                    ...prev,
                    [capabilityId]: {
                        success: false,
                        message: res?.error || res?.message || 'Sync failed',
                    },
                }));
            }
        } catch (err) {
            console.error('Failed to trigger sync', err);
            setSyncResult((prev) => ({
                ...prev,
                [capabilityId]: {
                    success: false,
                    message: err instanceof Error ? err.message : 'Sync request failed',
                },
            }));
        } finally {
            setSyncing(null);
        }
    };

    if (loading && daemons.length === 0) {
        return <SkeletonState count={4} lines={2} hasAvatar />;
    }

    if (loadError && daemons.length === 0) {
        return (
            <ErrorState title="Couldn't load capabilities" error={loadError} onRetry={refresh} />
        );
    }

    return (
        <div className="cap-page">
            <section className="cap-card">
                <header className="cap-card__header">
                    <ShieldCheck size={15} className="cap-card__header-icon" />
                    <span className="cap-card__title">Local capabilities</span>
                </header>
                <div className="cap-card__body">
                    <p className="cap-hint">
                        Built-in tools Chad can use — no account or credentials required. Everything here
                        defaults off; turning one on lets Chad read the listed local data. Adjust how often
                        it refreshes in Automations.
                    </p>
                    <div className="cap-list">
                        {CAPABILITY_CATALOG.map((capability) => {
                            const daemon = daemons.find((d) => d.id === capability.id);
                            const enabled = daemon?.enabled ?? false;
                            const Icon = capability.icon;
                            return (
                                <div key={capability.id} className={`cap-item ${enabled ? 'cap-item--on' : ''}`}>
                                    <div className="cap-item__top">
                                        <Icon size={16} className="cap-item__icon" />
                                        <span className="cap-item__name">{capability.name}</span>
                                        <label className="cap-toggle" title={enabled ? 'Disable' : 'Enable'}>
                                            <input
                                                type="checkbox"
                                                checked={enabled}
                                                disabled={saving === capability.id}
                                                onChange={(e) => toggleCapability(capability, e.target.checked)}
                                            />
                                            <span className="cap-toggle__track" />
                                        </label>
                                    </div>
                                    <ul className="cap-item__reads">
                                        {capability.reads.map((line) => (
                                             <li key={line}>{line}</li>
                                        ))}
                                    </ul>
                                    <div className="cap-item__footer">
                                        <div className="cap-item__footer-left">
                                            <span className={`cap-pill cap-pill--${enabled ? 'on' : 'off'}`}>
                                                {enabled ? 'Enabled' : 'Off'}
                                            </span>
                                            {syncResult[capability.id] && (
                                                <span
                                                    className={`cap-sync-msg ${
                                                        syncResult[capability.id].success
                                                            ? 'cap-sync-msg--success'
                                                            : 'cap-sync-msg--error'
                                                    }`}
                                                >
                                                    {syncResult[capability.id].message}
                                                </span>
                                            )}
                                        </div>
                                        {enabled && (
                                            <button
                                                type="button"
                                                className="cap-sync-btn"
                                                disabled={syncing === capability.id}
                                                onClick={() => handleSync(capability.id)}
                                                title="Sync history now"
                                            >
                                                <RefreshCw
                                                    size={12}
                                                    className={`cap-sync-icon ${
                                                        syncing === capability.id ? 'cap-sync-icon--spin' : ''
                                                    }`}
                                                />
                                                <span>{syncing === capability.id ? 'Syncing…' : 'Sync now'}</span>
                                            </button>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </section>
        </div>
    );
}
