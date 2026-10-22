import { useState, useEffect, useCallback } from 'react';
import { Loader2, Shield, AlertTriangle, Plus, X } from 'lucide-react';
import { useApiReady } from '../../../store';
import { readCache, writeCache } from '../../../utils/localCache';
import './BrowserSafetyTab.css';

type ApprovalMode = 'allow_all' | 'model_decide' | 'ask_all';

interface BrowserSafetySettings {
    approval_mode: ApprovalMode;
    allowlist: string[];
}

const MODE_OPTIONS: { value: ApprovalMode; label: string; description: string }[] = [
    {
        value: 'allow_all',
        label: 'Allow everything',
        description: 'Browser actions run without pausing — best for testing or trusted sessions.',
    },
    {
        value: 'model_decide',
        label: 'Let model decide',
        description: 'Pause on writes and on clicks/types at sites not in your allowlist.',
    },
    {
        value: 'ask_all',
        label: 'Ask everything',
        description: 'Pause on every browser action, including navigate and snapshot.',
    },
];

const FETCH_TIMEOUT_MS = 8000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
            (value) => { clearTimeout(timer); resolve(value); },
            (err) => { clearTimeout(timer); reject(err); },
        );
    });
}

function normalizeSettings(raw: unknown): BrowserSafetySettings {
    const data = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : {};
    const mode = data.approval_mode;
    const approval_mode: ApprovalMode =
        mode === 'model_decide' || mode === 'ask_all' ? mode : 'allow_all';
    const allowlist = Array.isArray(data.allowlist)
        ? data.allowlist.filter((e): e is string => typeof e === 'string')
        : [];
    return { approval_mode, allowlist };
}

export default function BrowserSafetyTab() {
    const apiReady = useApiReady();
    const [settings, setSettings] = useState<BrowserSafetySettings>(() => {
        return readCache<BrowserSafetySettings>('browser_safety_settings') ?? { approval_mode: 'allow_all', allowlist: [] };
    });
    const [loading, setLoading] = useState(() => !readCache<BrowserSafetySettings>('browser_safety_settings'));
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [newDomain, setNewDomain] = useState('');

    const api = apiReady ? window.pywebview?.api : undefined;

    const fetchSettings = useCallback(() => {
        if (!api?.get_browser_safety_settings) return;
        withTimeout(api.get_browser_safety_settings(), FETCH_TIMEOUT_MS, 'get_browser_safety_settings')
            .then((result) => {
                setError(null);
                const norm = normalizeSettings(result);
                setSettings(norm);
                writeCache('browser_safety_settings', norm);
            })
            .catch((e) => {
                console.error('[BrowserSafetyTab] load failed:', e);
                setError('Could not load browser safety settings. Check that the Brain server is running.');
            })
            .finally(() => setLoading(false));
    }, [api]);

    useEffect(() => {
        fetchSettings();
    }, [fetchSettings]);

    const persist = (next: BrowserSafetySettings) => {
        const bridge = window.pywebview?.api;
        if (!bridge?.set_browser_safety_settings) return;
        setSaving(true);
        bridge.set_browser_safety_settings(next.approval_mode, next.allowlist)
            .then((ok) => {
                if (!ok) throw new Error('save returned false');
                setSettings(next);
                writeCache('browser_safety_settings', next);
            })
            .catch((e) => {
                console.error('[BrowserSafetyTab] save failed:', e);
                setError('Failed to save browser safety settings.');
            })
            .finally(() => setSaving(false));
    };

    const handleModeChange = (mode: ApprovalMode) => {
        if (mode === settings.approval_mode || saving) return;
        persist({ ...settings, approval_mode: mode });
    };

    const handleAddDomain = () => {
        const trimmed = newDomain.trim().toLowerCase().replace(/^https?:\/\//, '').split('/')[0];
        if (!trimmed || settings.allowlist.includes(trimmed)) {
            setNewDomain('');
            return;
        }
        const next = { ...settings, allowlist: [...settings.allowlist, trimmed] };
        setNewDomain('');
        persist(next);
    };

    const handleRemoveDomain = (domain: string) => {
        const next = { ...settings, allowlist: settings.allowlist.filter((d) => d !== domain) };
        persist(next);
    };

    return (
        <div className="browser-safety-tab">
            <p className="browser-safety-tab__intro">
                Control when <code>browser_agent</code> pauses for your approval before running Playwright actions.
                Mode and allowlist apply together under &ldquo;Let model decide.&rdquo;
            </p>

            {loading && (
                <div className="browser-safety-loading">
                    <Loader2 size={18} className="browser-safety-spin" />
                    Loading browser safety settings…
                </div>
            )}

            {!loading && error && (
                <div className="browser-safety-error">
                    <AlertTriangle size={18} />
                    <span>{error}</span>
                </div>
            )}

            {!loading && !error && (
                <>
                    <section className="browser-safety-section">
                        <div className="browser-safety-section__head">
                            <Shield size={16} />
                            <h3>Approval mode</h3>
                            {saving && <Loader2 size={14} className="browser-safety-spin" />}
                        </div>
                        <div className="browser-safety-modes" role="radiogroup" aria-label="Browser approval mode">
                            {MODE_OPTIONS.map((opt) => (
                                <label
                                    key={opt.value}
                                    className={`browser-safety-mode ${settings.approval_mode === opt.value ? 'is-active' : ''}`}
                                >
                                    <input
                                        type="radio"
                                        name="browser_approval_mode"
                                        value={opt.value}
                                        checked={settings.approval_mode === opt.value}
                                        disabled={saving}
                                        onChange={() => handleModeChange(opt.value)}
                                    />
                                    <span className="browser-safety-mode__label">{opt.label}</span>
                                    <span className="browser-safety-mode__desc">{opt.description}</span>
                                </label>
                            ))}
                        </div>
                    </section>

                    <section className="browser-safety-section">
                        <div className="browser-safety-section__head">
                            <h3>Trusted domains</h3>
                            <span className="browser-safety-section__hint">
                                Used by &ldquo;Let model decide&rdquo; — subdomains count too.
                            </span>
                        </div>

                        <div className="browser-safety-allowlist">
                            {settings.allowlist.length === 0 ? (
                                <p className="browser-safety-empty">No trusted domains yet. Off-list sites get stricter checks.</p>
                            ) : (
                                <ul className="browser-safety-allowlist__list">
                                    {settings.allowlist.map((domain) => (
                                        <li key={domain} className="browser-safety-allowlist__item">
                                            <span>{domain}</span>
                                            <button
                                                type="button"
                                                className="browser-safety-allowlist__remove"
                                                onClick={() => handleRemoveDomain(domain)}
                                                disabled={saving}
                                                title={`Remove ${domain}`}
                                                aria-label={`Remove ${domain}`}
                                            >
                                                <X size={14} />
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>

                        <div className="browser-safety-add">
                            <input
                                type="text"
                                className="browser-safety-add__input"
                                placeholder="e.g. github.com"
                                value={newDomain}
                                disabled={saving}
                                onChange={(e) => setNewDomain(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') {
                                        e.preventDefault();
                                        handleAddDomain();
                                    }
                                }}
                            />
                            <button
                                type="button"
                                className="browser-safety-add__btn"
                                onClick={handleAddDomain}
                                disabled={saving || !newDomain.trim()}
                            >
                                <Plus size={16} />
                                Add
                            </button>
                        </div>
                    </section>
                </>
            )}
        </div>
    );
}
