import { useState, useEffect, useCallback } from 'react';
import { Loader2, AlertTriangle, RefreshCw, CheckCircle2, XCircle } from 'lucide-react';
import { useApiReady } from '../../../store';
import { readCache, writeCache } from '../../../utils/localCache';
import Dropdown from '../../common/Dropdown';
import './ChadCodeTab.css';

/** Chad Code settings tab (issue #574, epic #569).
 *
 * GUI for the OpenHands-backed local-model coding harness. Talks to the pywebview
 * bridge (`window.pywebview.api.chad_code_*`); degrades to a graceful no-op when the
 * bridge is absent. Mirrors PresentonSettingsTab's load/save/status shape. */

export interface ChadCodeSettings {
    coder_model: string;
    max_iterations: number;
    job_timeout_s: number;
    use_task_tracker: boolean;
    native_tool_calling: boolean;
    use_condenser: boolean;
    use_critic: boolean;
    context7_mcp: boolean;
    github_mcp: boolean;
    stream_events: boolean;
}

interface LocalModelOption {
    id: string;
    name: string;
}

interface PreflightCheck {
    ok: boolean;
    detail: string;
    soft?: boolean;
}

interface PreflightReport {
    ready: boolean;
    model?: string;
    image?: string;
    error?: string;
    checks: Record<string, PreflightCheck>;
}

interface JobStatusResult {
    job_id: string;
    status: string;
    exit_code?: string;
    workspace?: string;
    files?: string[];
    transcript_tail?: string[];
}

const FETCH_TIMEOUT_MS = 12000;
const PREFLIGHT_TIMEOUT_MS = 50000;
const JOB_TIMEOUT_MS = 35000;

const DEFAULTS: ChadCodeSettings = {
    coder_model: 'openai/qwen/qwen3.6-35b-a3b',
    max_iterations: 30,
    job_timeout_s: 1800,
    use_task_tracker: false,
    native_tool_calling: false,
    use_condenser: false,
    use_critic: false,
    context7_mcp: true,
    github_mcp: false,
    stream_events: true,
};

// Order + friendly labels for the preflight checks the backend returns.
const CHECK_LABELS: Record<string, string> = {
    docker: 'Docker daemon',
    image: 'OpenHands image',
    lmstudio: 'LM Studio',
    model: 'Coder model',
};
const CHECK_ORDER = ['docker', 'image', 'lmstudio', 'model'];

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
            (value) => { clearTimeout(timer); resolve(value); },
            (err) => { clearTimeout(timer); reject(err); },
        );
    });
}

function normalizeSettings(raw: unknown): ChadCodeSettings {
    const data = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : {};
    const num = (v: unknown, fallback: number) => {
        const n = Number(v);
        return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
    };
    const bool = (v: unknown, fallback: boolean) => typeof v === 'boolean' ? v : fallback;
    return {
        coder_model: typeof data.coder_model === 'string' && data.coder_model ? data.coder_model : DEFAULTS.coder_model,
        max_iterations: num(data.max_iterations, DEFAULTS.max_iterations),
        job_timeout_s: num(data.job_timeout_s, DEFAULTS.job_timeout_s),
        use_task_tracker: bool(data.use_task_tracker, DEFAULTS.use_task_tracker),
        native_tool_calling: bool(data.native_tool_calling, DEFAULTS.native_tool_calling),
        use_condenser: bool(data.use_condenser, DEFAULTS.use_condenser),
        use_critic: bool(data.use_critic, DEFAULTS.use_critic),
        context7_mcp: bool(data.context7_mcp, DEFAULTS.context7_mcp),
        github_mcp: bool(data.github_mcp, DEFAULTS.github_mcp),
        stream_events: bool(data.stream_events, DEFAULTS.stream_events),
    };
}

function shortModelName(name: string): string {
    return name.split('/').pop()?.replace(/-/g, ' ') ?? name;
}

export default function ChadCodeTab() {
    const apiReady = useApiReady();
    const [settings, setSettings] = useState<ChadCodeSettings>(() => {
        const cached = readCache<ChadCodeSettings>('chad_code_settings');
        return cached ? normalizeSettings(cached) : DEFAULTS;
    });
    const [models, setModels] = useState<LocalModelOption[]>(() => {
        return readCache<LocalModelOption[]>('chad_code_local_models') ?? [];
    });
    const [loading, setLoading] = useState(() => !readCache<ChadCodeSettings>('chad_code_settings'));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [status, setStatus] = useState<string | null>(null);

    const [preflight, setPreflight] = useState<PreflightReport | null>(null);
    const [preflightLoading, setPreflightLoading] = useState(false);

    const [jobId, setJobId] = useState('');
    const [job, setJob] = useState<JobStatusResult | null>(null);
    const [jobError, setJobError] = useState<string | null>(null);
    const [jobLoading, setJobLoading] = useState(false);

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
            writeCache('chad_code_local_models', opts);
        } catch (e) {
            console.warn('[ChadCodeTab] model discovery failed:', e);
            setModels([]);
        }
    }, [api]);

    const fetchSettings = useCallback(() => {
        if (!api?.get_chad_code_settings) {
            setLoading(false);
            return;
        }
        withTimeout(api.get_chad_code_settings(), FETCH_TIMEOUT_MS, 'get_chad_code_settings')
            .then((result) => {
                setError(null);
                const norm = normalizeSettings(result);
                setSettings(norm);
                writeCache('chad_code_settings', norm);
            })
            .catch((e) => {
                console.error('[ChadCodeTab] load failed:', e);
                setError('Could not load Chad Code settings.');
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

    const persist = (next: ChadCodeSettings) => {
        const bridge = window.pywebview?.api;
        if (!bridge?.set_chad_code_settings) return;
        setSaving(true);
        setError(null);
        setStatus(null);
        bridge.set_chad_code_settings(
            next.coder_model || null,
            next.max_iterations,
            next.job_timeout_s,
            next.use_task_tracker,
            next.native_tool_calling,
            next.use_condenser,
            next.use_critic,
            next.context7_mcp,
            next.github_mcp,
            next.stream_events,
        )
            .then((result) => {
                const payload = (result && typeof result === 'object') ? result as Record<string, unknown> : {};
                if (payload.success === false) {
                    throw new Error(String(payload.error || 'save failed'));
                }
                const norm = normalizeSettings(payload);
                setSettings(norm);
                writeCache('chad_code_settings', norm);
                setStatus('Settings saved.');
            })
            .catch((e) => {
                console.error('[ChadCodeTab] save failed:', e);
                setStatus(e instanceof Error ? e.message : 'Failed to save Chad Code settings.');
            })
            .finally(() => setSaving(false));
    };

    const handleFieldBlur = () => {
        if (saving) return;
        persist(settings);
    };

    const runPreflight = () => {
        const bridge = window.pywebview?.api;
        if (!bridge?.chad_code_preflight) {
            setStatus('Preflight requires the desktop app.');
            return;
        }
        setPreflightLoading(true);
        setPreflight(null);
        withTimeout(bridge.chad_code_preflight(), PREFLIGHT_TIMEOUT_MS, 'chad_code_preflight')
            .then((result) => {
                const report = (result && typeof result === 'object') ? result as unknown as PreflightReport : null;
                setPreflight(report && report.checks ? report : { ready: false, error: 'Malformed preflight response.', checks: {} });
            })
            .catch((e) => {
                console.error('[ChadCodeTab] preflight failed:', e);
                setPreflight({ ready: false, error: e instanceof Error ? e.message : 'Preflight failed.', checks: {} });
            })
            .finally(() => setPreflightLoading(false));
    };

    const checkJob = () => {
        const trimmed = jobId.trim();
        if (!trimmed) {
            setJobError('Enter a job id first.');
            return;
        }
        const bridge = window.pywebview?.api;
        if (!bridge?.chad_code_job_status) {
            setJobError('Job status requires the desktop app.');
            return;
        }
        setJobLoading(true);
        setJobError(null);
        setJob(null);
        withTimeout(bridge.chad_code_job_status(trimmed), JOB_TIMEOUT_MS, 'chad_code_job_status')
            .then((result) => {
                const payload = (result && typeof result === 'object') ? result as Record<string, unknown> : {};
                if (payload.success === false) {
                    throw new Error(String(payload.error || 'No such coding job.'));
                }
                const res = (payload.result && typeof payload.result === 'object')
                    ? payload.result as unknown as JobStatusResult : null;
                if (!res) throw new Error('Malformed job status response.');
                setJob(res);
            })
            .catch((e) => {
                console.error('[ChadCodeTab] job status failed:', e);
                setJobError(e instanceof Error ? e.message : 'Failed to fetch job status.');
            })
            .finally(() => setJobLoading(false));
    };

    const orderedChecks = preflight
        ? [...CHECK_ORDER.filter((k) => k in preflight.checks),
           ...Object.keys(preflight.checks).filter((k) => !CHECK_ORDER.includes(k))]
        : [];

    return (
        <div className="chadcode-settings">
            <p className="chadcode-settings__intro">
                Chad Code delegates real coding work to a local model via the OpenHands harness
                (Docker + LM Studio), as background jobs. Run the check below to confirm the
                prerequisites, then tune the coder model and limits.
            </p>

            {/* ── Preflight ── */}
            <div className="chadcode-settings__section">
                <div className="chadcode-settings__section-head">
                    <span className="chadcode-settings__section-title">Prerequisites</span>
                    <button
                        type="button"
                        className="settings-update-btn settings-update-btn--secondary"
                        onClick={runPreflight}
                        disabled={preflightLoading}
                    >
                        <RefreshCw size={14} className={preflightLoading ? 'chadcode-settings__spin' : ''} />
                        {preflightLoading ? 'Checking…' : 'Check / set up Chad Code'}
                    </button>
                </div>

                {preflight && (
                    <div className="chadcode-settings__checks">
                        {preflight.error && (
                            <div className="chadcode-settings__error">
                                <AlertTriangle size={16} />
                                <span>{preflight.error}</span>
                            </div>
                        )}
                        {orderedChecks.map((key) => {
                            const c = preflight.checks[key];
                            return (
                                <div key={key} className="chadcode-settings__check">
                                    {c.ok
                                        ? <CheckCircle2 size={16} className="chadcode-settings__check-ok" />
                                        : <XCircle size={16} className={c.soft ? 'chadcode-settings__check-warn' : 'chadcode-settings__check-bad'} />}
                                    <div className="chadcode-settings__check-body">
                                        <span className="chadcode-settings__check-name">
                                            {CHECK_LABELS[key] ?? key}
                                            {c.soft && <span className="chadcode-settings__check-soft"> (optional)</span>}
                                        </span>
                                        <span className="chadcode-settings__check-detail">{c.detail}</span>
                                    </div>
                                </div>
                            );
                        })}
                        <p className={`chadcode-settings__ready chadcode-settings__ready--${preflight.ready ? 'ok' : 'bad'}`}>
                            {preflight.ready
                                ? 'Ready — required checks pass. You can dispatch coding jobs.'
                                : 'Not ready — resolve the required checks above (optional ones only warn).'}
                        </p>
                    </div>
                )}
            </div>

            {/* ── Config ── */}
            {loading && (
                <div className="chadcode-settings__loading">
                    <Loader2 size={18} className="chadcode-settings__spin" />
                    Loading Chad Code settings…
                </div>
            )}

            {!loading && error && (
                <div className="chadcode-settings__error">
                    <AlertTriangle size={18} />
                    <span>{error}</span>
                </div>
            )}

            {!loading && !error && (
                <div className="chadcode-settings__section">
                    <span className="chadcode-settings__section-title">Configuration</span>
                    <div className="chadcode-settings__fields">
                        <div className="chadcode-settings__field">
                            <div className="chadcode-settings__field-head">
                                <label className="form-label" htmlFor="chadcode-model">Coder model</label>
                                <button
                                    type="button"
                                    className="chadcode-settings__refresh"
                                    onClick={refreshModels}
                                    disabled={saving}
                                    title="Refresh model list from LM Studio"
                                >
                                    <RefreshCw size={14} />
                                </button>
                            </div>
                            {models.length > 0 ? (
                                <Dropdown
                                    value={settings.coder_model}
                                    options={models.map((m) => ({ value: m.id, label: m.name }))}
                                    onChange={(value) => {
                                        const next = { ...settings, coder_model: value };
                                        setSettings(next);
                                        persist(next);
                                    }}
                                    disabled={saving}
                                />
                            ) : (
                                <input
                                    id="chadcode-model"
                                    type="text"
                                    className="chadcode-settings__input"
                                    value={settings.coder_model}
                                    disabled={saving}
                                    onChange={(e) => setSettings({ ...settings, coder_model: e.target.value })}
                                    onBlur={handleFieldBlur}
                                    placeholder="openai/qwen/qwen3.6-35b-a3b"
                                />
                            )}
                            <p className="settings-hint">LiteLLM model id served by LM Studio (keep the <code>openai/</code> prefix).</p>
                        </div>

                        <div className="chadcode-settings__field chadcode-settings__field--row">
                            <div className="chadcode-settings__field-col">
                                <label className="form-label" htmlFor="chadcode-max-iter">Max iterations</label>
                                <input
                                    id="chadcode-max-iter"
                                    type="number"
                                    min={1}
                                    className="chadcode-settings__input"
                                    value={settings.max_iterations}
                                    disabled={saving}
                                    onChange={(e) => setSettings({ ...settings, max_iterations: Number(e.target.value) })}
                                    onBlur={handleFieldBlur}
                                />
                            </div>
                            <div className="chadcode-settings__field-col">
                                <label className="form-label" htmlFor="chadcode-timeout">Job timeout (seconds)</label>
                                <input
                                    id="chadcode-timeout"
                                    type="number"
                                    min={1}
                                    className="chadcode-settings__input"
                                    value={settings.job_timeout_s}
                                    disabled={saving}
                                    onChange={(e) => setSettings({ ...settings, job_timeout_s: Number(e.target.value) })}
                                    onBlur={handleFieldBlur}
                                />
                            </div>
                        </div>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.use_task_tracker}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, use_task_tracker: !settings.use_task_tracker };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>Give the agent a task tracker (plan/checklist) — pays off only on large multi-step tasks</span>
                        </label>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.native_tool_calling}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, native_tool_calling: !settings.native_tool_calling };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>Native tool calling — only for models that emit real OpenAI tool_calls (off for mid-size local coders)</span>
                        </label>

                        <span className="chadcode-settings__section-title chadcode-settings__section-title--sub">MCP servers</span>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.context7_mcp}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, context7_mcp: !settings.context7_mcp };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>Context7 MCP — hosted docs lookup, no auth needed</span>
                        </label>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.github_mcp}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, github_mcp: !settings.github_mcp };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>GitHub MCP — needs a GITHUB_PERSONAL_ACCESS_TOKEN configured on the host</span>
                        </label>

                        <span className="chadcode-settings__section-title chadcode-settings__section-title--sub">Advanced</span>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.use_condenser}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, use_condenser: !settings.use_condenser };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>Context condenser — summarizes old history on long jobs so they can't die of context overflow</span>
                        </label>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.use_critic}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, use_critic: !settings.use_critic };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>Verification critic — flags and retries a run that finishes with no code changes</span>
                        </label>

                        <label className="chadcode-settings__toggle">
                            <input
                                type="checkbox"
                                checked={settings.stream_events}
                                disabled={saving}
                                onChange={() => {
                                    const next = { ...settings, stream_events: !settings.stream_events };
                                    setSettings(next);
                                    persist(next);
                                }}
                            />
                            <span>Live event streaming — CHAD_EVENT: lines visible via docker logs while a job runs</span>
                        </label>

                        {saving && (
                            <div className="chadcode-settings__saving">
                                <Loader2 size={14} className="chadcode-settings__spin" /> Saving…
                            </div>
                        )}
                        {status && <p className="settings-hint chadcode-settings__status">{status}</p>}
                    </div>
                </div>
            )}

            {/* ── Job panel ── */}
            <div className="chadcode-settings__section">
                <span className="chadcode-settings__section-title">Check a coding job</span>
                <div className="chadcode-settings__job-input">
                    <input
                        type="text"
                        className="chadcode-settings__input"
                        value={jobId}
                        onChange={(e) => setJobId(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') checkJob(); }}
                        placeholder="job id (from dispatch_coding_task)"
                        disabled={jobLoading}
                    />
                    <button
                        type="button"
                        className="settings-update-btn settings-update-btn--secondary"
                        onClick={checkJob}
                        disabled={jobLoading}
                    >
                        <RefreshCw size={14} className={jobLoading ? 'chadcode-settings__spin' : ''} />
                        {jobLoading ? 'Checking…' : 'Check'}
                    </button>
                </div>

                {jobError && (
                    <div className="chadcode-settings__error">
                        <AlertTriangle size={16} />
                        <span>{jobError}</span>
                    </div>
                )}

                {job && (
                    <div className="chadcode-settings__job">
                        <p className="chadcode-settings__job-status">
                            Job <code>{job.job_id}</code>: <strong className={`chadcode-settings__job-badge chadcode-settings__job-badge--${job.status}`}>{job.status}</strong>
                            {job.exit_code !== undefined && job.exit_code !== '' && <span className="settings-hint"> (exit {job.exit_code})</span>}
                        </p>
                        {job.workspace && <p className="settings-hint chadcode-settings__job-ws">Workspace: <code>{job.workspace}</code></p>}

                        {job.files && job.files.length > 0 && (
                            <div className="chadcode-settings__job-block">
                                <span className="chadcode-settings__job-block-title">Files ({job.files.length})</span>
                                <ul className="chadcode-settings__file-list">
                                    {job.files.map((f) => <li key={f}>{f}</li>)}
                                </ul>
                            </div>
                        )}

                        {job.transcript_tail && job.transcript_tail.length > 0 && (
                            <div className="chadcode-settings__job-block">
                                <span className="chadcode-settings__job-block-title">Transcript tail</span>
                                <pre className="chadcode-settings__transcript">{job.transcript_tail.join('\n')}</pre>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
