import { useState, useEffect, useCallback } from 'react';
import { Loader2, AlertTriangle, RefreshCw } from 'lucide-react';
import { useApiReady } from '../../../store';
import { readCache, writeCache } from '../../../utils/localCache';
import Dropdown from '../../common/Dropdown';
import './PresentonSettingsTab.css';

export interface PresentonSettings {
    enabled: boolean;
    url: string;
    llm_url: string;
    model: string;
    use_async: boolean;
}

interface LocalModelOption {
    id: string;
    name: string;
}

const FETCH_TIMEOUT_MS = 12000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
            (value) => { clearTimeout(timer); resolve(value); },
            (err) => { clearTimeout(timer); reject(err); },
        );
    });
}

function normalizeSettings(raw: unknown): PresentonSettings {
    const data = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : {};
    return {
        enabled: Boolean(data.enabled),
        url: typeof data.url === 'string' ? data.url : '',
        llm_url: typeof data.llm_url === 'string' ? data.llm_url : 'http://host.docker.internal:1234/v1',
        model: typeof data.model === 'string' ? data.model : '',
        use_async: Boolean(data.use_async),
    };
}

function shortModelName(name: string): string {
    return name.split('/').pop()?.replace(/-/g, ' ') ?? name;
}

interface PresentonSettingsTabProps {
    onSaved?: () => void;
    connected?: boolean;
}

export default function PresentonSettingsTab({ onSaved, connected }: PresentonSettingsTabProps) {
    const apiReady = useApiReady();
    const [settings, setSettings] = useState<PresentonSettings>(() => {
        const cached = readCache<PresentonSettings>('presenton_settings');
        return cached ? normalizeSettings(cached) : {
            enabled: false,
            url: '',
            llm_url: 'http://host.docker.internal:1234/v1',
            model: '',
            use_async: false,
        };
    });
    const [models, setModels] = useState<LocalModelOption[]>(() => {
        return readCache<LocalModelOption[]>('presenton_local_models') ?? [];
    });
    const [loading, setLoading] = useState(() => !readCache<PresentonSettings>('presenton_settings'));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [status, setStatus] = useState<string | null>(null);

    const api = apiReady ? window.pywebview?.api : undefined;

    const refreshModels = useCallback(async () => {
        if (!api?.discover_local_models) return;
        try {
            const discovered = await withTimeout(
                api.discover_local_models(),
                FETCH_TIMEOUT_MS,
                'discover_local_models',
            );
            if (!Array.isArray(discovered)) {
                setModels([]);
                return;
            }
            const opts = discovered
                .map((m: Record<string, unknown>) => {
                    const id = String(m.name ?? m.id ?? '');
                    if (!id) return null;
                    return { id, name: shortModelName(id) };
                })
                .filter((m): m is LocalModelOption => m !== null);
            setModels(opts);
            writeCache('presenton_local_models', opts);
        } catch (e) {
            console.warn('[PresentonSettingsTab] model discovery failed:', e);
            setModels([]);
        }
    }, [api]);

    const fetchSettings = useCallback(() => {
        if (!api?.get_presenton_settings) return;
        withTimeout(api.get_presenton_settings(), FETCH_TIMEOUT_MS, 'get_presenton_settings')
            .then((result) => {
                setError(null);
                const norm = normalizeSettings(result);
                setSettings(norm);
                writeCache('presenton_settings', norm);
            })
            .catch((e) => {
                console.error('[PresentonSettingsTab] load failed:', e);
                setError('Could not load Presenton settings.');
            })
            .finally(() => setLoading(false));
    }, [api]);

    useEffect(() => {
        let cancelled = false;
        void Promise.resolve().then(() => {
            if (cancelled) return;
            fetchSettings();
            void refreshModels();
        });
        return () => { cancelled = true; };
    }, [fetchSettings, refreshModels]);

    const persist = (next: PresentonSettings, connectSidecar = false) => {
        const bridge = window.pywebview?.api;
        if (!bridge?.set_presenton_settings) return;
        setSaving(true);
        setError(null);
        setStatus(null);
        bridge.set_presenton_settings(
            next.enabled,
            next.url || null,
            next.llm_url || null,
            next.model || null,
            next.use_async,
            connectSidecar,
        )
            .then((result) => {
                const payload = (result && typeof result === 'object') ? result as Record<string, unknown> : {};
                if (payload.success === false) {
                    throw new Error(String(payload.error || 'save failed'));
                }
                const merged = normalizeSettings(payload.settings ?? next);
                setSettings(merged);
                writeCache('presenton_settings', merged);
                const sidecarMsg = typeof payload.sidecar_message === 'string' ? payload.sidecar_message : '';
                if (sidecarMsg) {
                    setStatus(sidecarMsg);
                } else {
                    setStatus('Settings saved.');
                }
                onSaved?.();
            })
            .catch((e) => {
                console.error('[PresentonSettingsTab] save failed:', e);
                setStatus(e instanceof Error ? e.message : 'Failed to save Presenton settings.');
            })
            .finally(() => setSaving(false));
    };

    const handleToggleEnabled = () => {
        const next = { ...settings, enabled: !settings.enabled };
        if (next.enabled && !next.url) {
            next.url = 'http://127.0.0.1:5000';
        }
        persist(next);
    };

    const handleFieldBlur = () => {
        if (saving) return;
        persist(settings);
    };

    const handleConnect = () => {
        if (!settings.model.trim()) {
            setStatus('Choose a deck generation model before connecting Presenton.');
            return;
        }
        persist(settings, true);
    };

    return (
        <div className="presenton-settings">
            <p className="presenton-settings__intro">
                Connection status: <strong>{connected === undefined ? 'Checking…' : connected ? 'Connected' : 'Not connected'}</strong>.
                {' '}Changing the deck model or LLM endpoint updates the local Docker sidecar automatically.
                {' '}Use Connect only when Presenton is not running yet.
            </p>

            {loading && (
                <div className="presenton-settings__loading">
                    <Loader2 size={18} className="presenton-settings__spin" />
                    Loading Presenton settings…
                </div>
            )}

            {!loading && error && (
                <div className="presenton-settings__error">
                    <AlertTriangle size={18} />
                    <span>{error}</span>
                </div>
            )}

            {!loading && !error && (
                <>
                    <label className="presenton-settings__toggle">
                        <input
                            type="checkbox"
                            checked={settings.enabled}
                            disabled={saving}
                            onChange={handleToggleEnabled}
                        />
                        <span>Enable Presenton (<code>generate_presentation</code>)</span>
                        {saving && <Loader2 size={14} className="presenton-settings__spin" />}
                    </label>

                    {settings.enabled && (
                        <div className="presenton-settings__fields">
                            <div className="presenton-settings__field">
                                <label className="form-label" htmlFor="presenton-url">Sidecar URL</label>
                                <input
                                    id="presenton-url"
                                    type="text"
                                    className="presenton-settings__input"
                                    value={settings.url}
                                    disabled={saving}
                                    onChange={(e) => setSettings({ ...settings, url: e.target.value })}
                                    onBlur={handleFieldBlur}
                                    placeholder="http://127.0.0.1:5000"
                                />
                            </div>

                            <div className="presenton-settings__field">
                                <label className="form-label" htmlFor="presenton-llm-url">LLM endpoint (inside Docker)</label>
                                <input
                                    id="presenton-llm-url"
                                    type="text"
                                    className="presenton-settings__input"
                                    value={settings.llm_url}
                                    disabled={saving}
                                    onChange={(e) => setSettings({ ...settings, llm_url: e.target.value })}
                                    onBlur={handleFieldBlur}
                                    placeholder="http://host.docker.internal:1234/v1"
                                />
                                <p className="settings-hint">Use <code>host.docker.internal</code> so the container reaches LM Studio on your PC.</p>
                            </div>

                            <div className="presenton-settings__field">
                                <div className="presenton-settings__field-head">
                                    <label className="form-label" htmlFor="presenton-model">Deck generation model</label>
                                    <button
                                        type="button"
                                        className="presenton-settings__refresh"
                                        onClick={refreshModels}
                                        disabled={saving}
                                        title="Refresh model list from LM Studio"
                                    >
                                        <RefreshCw size={14} />
                                    </button>
                                </div>
                                {models.length > 0 ? (
                                    <Dropdown
                                        value={settings.model}
                                        options={models.map((m) => ({ value: m.id, label: m.name }))}
                                        onChange={(value) => {
                                            const next = { ...settings, model: value };
                                            setSettings(next);
                                            persist(next);
                                        }}
                                        disabled={saving}
                                    />
                                ) : (
                                    <input
                                        id="presenton-model"
                                        type="text"
                                        className="presenton-settings__input"
                                        value={settings.model}
                                        disabled={saving}
                                        onChange={(e) => setSettings({ ...settings, model: e.target.value })}
                                        onBlur={handleFieldBlur}
                                        placeholder="qwen/qwen3.5-9b"
                                    />
                                )}
                            </div>

                            <label className="presenton-settings__toggle presenton-settings__toggle--sub">
                                <input
                                    type="checkbox"
                                    checked={settings.use_async}
                                    disabled={saving}
                                    onChange={() => {
                                        const next = { ...settings, use_async: !settings.use_async };
                                        setSettings(next);
                                        persist(next);
                                    }}
                                />
                                <span>Use async API (v3 poll) — for cloud or long decks</span>
                            </label>

                            {connected === false && (
                                <button
                                    type="button"
                                    className="settings-update-btn settings-update-btn--secondary"
                                    onClick={handleConnect}
                                    disabled={saving}
                                >
                                    <RefreshCw size={14} className={saving ? 'presenton-settings__spin' : ''} />
                                    {saving ? 'Connecting…' : 'Connect / set up Presenton'}
                                </button>
                            )}
                        </div>
                    )}

                    {status && <p className="settings-hint presenton-settings__status">{status}</p>}
                </>
            )}
        </div>
    );
}
