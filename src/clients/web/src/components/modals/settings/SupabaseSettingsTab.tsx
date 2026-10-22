import { useState, useEffect, useCallback } from 'react';
import { Loader2, Cloud, CheckCircle2, AlertTriangle } from 'lucide-react';
import { useApiReady } from '../../../store';
import './SupabaseSettingsTab.css';

export interface SupabaseSettings {
    configured: boolean;
    url: string;
    anon_key: string;
    service_key: string;
    db_url: string;
    has_service_key?: boolean;
}

const FETCH_TIMEOUT_MS = 12000;
const BOOTSTRAP_TIMEOUT_MS = 600000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
            (value) => { clearTimeout(timer); resolve(value); },
            (err) => { clearTimeout(timer); reject(err); },
        );
    });
}

function normalizeSettings(raw: unknown): SupabaseSettings {
    const data = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : {};
    return {
        configured: Boolean(data.configured),
        url: typeof data.url === 'string' ? data.url : '',
        anon_key: typeof data.anon_key === 'string' ? data.anon_key : '',
        service_key: typeof data.service_key === 'string' ? data.service_key : '',
        db_url: typeof data.db_url === 'string' ? data.db_url : '',
        has_service_key: Boolean(data.has_service_key),
    };
}

export default function SupabaseSettingsTab() {
    const apiReady = useApiReady();
    const [status, setStatus] = useState<SupabaseSettings>({
        configured: false,
        url: '',
        anon_key: '',
        service_key: '',
        db_url: '',
    });
    const [url, setUrl] = useState('');
    const [anonKey, setAnonKey] = useState('');
    const [serviceKey, setServiceKey] = useState('');
    const [dbUrl, setDbUrl] = useState('');
    const [loading, setLoading] = useState(true);
    const [bootstrapping, setBootstrapping] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [steps, setSteps] = useState<string[]>([]);

    const api = apiReady ? window.pywebview?.api : undefined;

    const fetchStatus = useCallback(() => {
        if (!api?.get_supabase_settings) return;
        setLoading(true);
        setError(null);
        withTimeout(api.get_supabase_settings(), FETCH_TIMEOUT_MS, 'get_supabase_settings')
            .then((result) => {
                const normalized = normalizeSettings(result);
                setStatus(normalized);
                if (normalized.url) setUrl(normalized.url);
                if (normalized.db_url && !normalized.db_url.includes('…')) {
                    setDbUrl(normalized.db_url);
                }
            })
            .catch((e) => {
                console.error('[SupabaseSettingsTab] load failed:', e);
                setError('Could not load Supabase status.');
            })
            .finally(() => setLoading(false));
    }, [api]);

    useEffect(() => {
        fetchStatus();
    }, [fetchStatus]);

    const handleBootstrap = async () => {
        if (!api?.bootstrap_supabase) return;
        setBootstrapping(true);
        setError(null);
        setMessage(null);
        setSteps([]);
        try {
            const result = await withTimeout(
                api.bootstrap_supabase(url, anonKey, serviceKey, dbUrl),
                BOOTSTRAP_TIMEOUT_MS,
                'bootstrap_supabase',
            ) as { success?: boolean; error?: string; message?: string; steps?: string[] };

            if (result?.steps) setSteps(result.steps);
            if (!result?.success) {
                setError(result?.error ?? 'Bootstrap failed.');
                return;
            }
            setMessage(result.message ?? 'Supabase connected and provisioned.');
            fetchStatus();
        } catch (e) {
            setError(String(e));
        } finally {
            setBootstrapping(false);
        }
    };

    if (loading) {
        return (
            <div className="supabase-settings supabase-settings--loading">
                <Loader2 size={18} className="is-spinning" />
                <span>Checking Supabase status…</span>
            </div>
        );
    }

    return (
        <div className="supabase-settings">
            <div className="supabase-settings__intro">
                <Cloud size={20} />
                <div>
                    <p className="supabase-settings__headline">
                        {status.configured ? 'Supabase connected' : 'Want better memory?'}
                    </p>
                    <p className="settings-hint">
                        Chad works without Supabase. Connect a project for richer memory, tool RAG,
                        persona editing, and cross-device chat history sync.
                    </p>
                </div>
                {status.configured && (
                    <span className="supabase-settings__badge" title="Supabase credentials detected">
                        <CheckCircle2 size={14} />
                        Connected
                    </span>
                )}
            </div>

            <div className="form-group">
                <label className="form-label" htmlFor="supabase-url">Project URL</label>
                <input
                    id="supabase-url"
                    type="url"
                    className="supabase-settings__input"
                    placeholder="https://your-project-ref.supabase.co"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    autoComplete="off"
                />
            </div>

            <div className="form-group">
                <label className="form-label" htmlFor="supabase-anon">Anon key (publishable)</label>
                <input
                    id="supabase-anon"
                    type="password"
                    className="supabase-settings__input"
                    placeholder={status.anon_key || 'sb_publishable_…'}
                    value={anonKey}
                    onChange={(e) => setAnonKey(e.target.value)}
                    autoComplete="off"
                />
            </div>

            <div className="form-group">
                <label className="form-label" htmlFor="supabase-service">
                    Service role key <span className="supabase-settings__optional">(recommended)</span>
                </label>
                <input
                    id="supabase-service"
                    type="password"
                    className="supabase-settings__input"
                    placeholder={status.service_key || 'sb_secret_…'}
                    value={serviceKey}
                    onChange={(e) => setServiceKey(e.target.value)}
                    autoComplete="off"
                />
            </div>

            <div className="form-group">
                <label className="form-label" htmlFor="supabase-db">Database connection string</label>
                <input
                    id="supabase-db"
                    type="password"
                    className="supabase-settings__input"
                    placeholder={status.db_url || 'postgresql://postgres.…'}
                    value={dbUrl}
                    onChange={(e) => setDbUrl(e.target.value)}
                    autoComplete="off"
                />
                <p className="settings-hint">
                    Supabase dashboard → Settings → Database → Connection string (URI, Session pooler).
                </p>
            </div>

            <button
                type="button"
                className="settings-update-btn"
                onClick={handleBootstrap}
                disabled={bootstrapping || !url.trim() || !anonKey.trim() || !dbUrl.trim()}
            >
                {bootstrapping ? (
                    <>
                        <Loader2 size={16} className="is-spinning" />
                        Provisioning… (may take a few minutes)
                    </>
                ) : (
                    status.configured ? 'Re-run provisioning' : 'Connect & provision'
                )}
            </button>

            {error && (
                <p className="settings-hint settings-audio-result is-error">
                    <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                    {error}
                </p>
            )}
            {message && (
                <p className="settings-hint settings-audio-result is-success">{message}</p>
            )}
            {steps.length > 0 && (
                <ul className="supabase-settings__steps">
                    {steps.map((step) => (
                        <li key={step}>{step}</li>
                    ))}
                </ul>
            )}
        </div>
    );
}
