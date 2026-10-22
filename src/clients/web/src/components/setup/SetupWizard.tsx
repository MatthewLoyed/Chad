/**
 * Full-screen first-run setup wizard (issue #428, backend chooser added #499) — shown
 * on desktop (pywebview) transport when a fresh CHAD_HOME hasn't finished local model
 * setup yet. Chad is local-only: no cloud API key step. Steps —
 *   1. Welcome (what Chad can do)
 *   2. Backend chooser — LM Studio (recommended, one-click `lms` CLI flow reusing the
 *      model catalog + download primitives from #419) / Ollama / any other OpenAI-
 *      compatible endpoint (llama.cpp server, vLLM). Chad only probes Ollama/custom
 *      endpoints and lets the user pick from what it finds — it doesn't script or
 *      manage servers it doesn't own (issue #450 / #499).
 *   3. MCP connectors (reuses the existing Connectors UI from #217)
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, CheckCircle2, XCircle, ExternalLink, Server } from 'lucide-react';
import ConnectorsTab from '../modals/settings/ConnectorsTab';
import '../modals/settings/MCPServersTab.css';

type Step = 'welcome' | 'backend-choice' | 'lmstudio' | 'ollama' | 'custom' | 'connectors';

interface RequiredModel { model_id: string; role: string; approx_vram_gb: number; }

interface SetupStatus {
    needs_setup?: boolean;
    lms_cli_found?: boolean;
    server_running?: boolean;
    models_missing?: string[];
    required_models?: RequiredModel[];
    lmstudio_download_url?: string;
}

interface LmSetupProgress {
    running?: boolean;
    done?: boolean;
    log?: string[];
    report?: { ok?: boolean; errors?: string[]; models_present?: string[]; models_downloaded?: string[]; models_missing?: string[]; server_running?: boolean } | null;
}

interface MCPServer { name: string; enabled: boolean; connected: boolean; configured?: boolean; }
interface NativeConnector { id: string; enabled: boolean; configured: boolean; connected: boolean; }

interface ProbedModel { id: string; display_name: string; supports_tools: boolean; }
interface ProbeResult { reachable?: boolean; models?: ProbedModel[]; download_url?: string | null; }

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
    const [step, setStep] = useState<Step>('welcome');
    const [status, setStatus] = useState<SetupStatus>({});
    const [progress, setProgress] = useState<LmSetupProgress>({});
    const [checking, setChecking] = useState(false);
    const pollRef = useRef<number | null>(null);

    const [servers, setServers] = useState<MCPServer[]>([]);
    const [nativeConnectors, setNativeConnectors] = useState<NativeConnector[]>([]);

    const [customBaseUrl, setCustomBaseUrl] = useState('http://localhost:11434/v1');
    const [customApiKey, setCustomApiKey] = useState('');
    const [probing, setProbing] = useState(false);
    const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
    const [selectedModel, setSelectedModel] = useState<string>('');
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    const api = window.pywebview?.api;

    const startOllama = () => {
        setCustomBaseUrl('http://localhost:11434/v1');
        setCustomApiKey('');
        setProbeResult(null);
        setSelectedModel('');
        setSaveError(null);
        setStep('ollama');
    };

    const startCustom = () => {
        setCustomBaseUrl('http://localhost:8000/v1');
        setCustomApiKey('');
        setProbeResult(null);
        setSelectedModel('');
        setSaveError(null);
        setStep('custom');
    };

    const probeBackend = async (provider: 'ollama' | 'custom') => {
        if (!api?.probe_local_backend) return;
        setProbing(true);
        setSaveError(null);
        try {
            const result = await api.probe_local_backend(provider, customBaseUrl, customApiKey) as ProbeResult;
            setProbeResult(result ?? {});
            const models = result?.models ?? [];
            setSelectedModel(models.length ? models[0].id : '');
        } finally {
            setProbing(false);
        }
    };

    const confirmProviderChoice = async (provider: 'ollama' | 'custom') => {
        if (!api?.save_provider_choice || !selectedModel) return;
        setSaving(true);
        setSaveError(null);
        try {
            const model = probeResult?.models?.find(m => m.id === selectedModel);
            const result = await api.save_provider_choice(
                provider, customBaseUrl, customApiKey, selectedModel, model?.supports_tools ?? true,
            ) as { success?: boolean; error?: string };
            if (result?.success) {
                setStep('connectors');
            } else {
                setSaveError(result?.error || 'Could not save this choice.');
            }
        } finally {
            setSaving(false);
        }
    };

    const refreshStatus = useCallback(async () => {
        if (!api?.get_setup_status) return;
        setChecking(true);
        try {
            const result = await api.get_setup_status() as SetupStatus;
            setStatus(result ?? {});
        } finally {
            setChecking(false);
        }
    }, [api]);

    useEffect(() => { refreshStatus(); }, [refreshStatus]);

    const stopPolling = () => {
        if (pollRef.current !== null) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
        }
    };

    useEffect(() => () => stopPolling(), []);

    const startLmStudioSetup = async () => {
        if (!api?.start_lmstudio_setup || !api?.get_lmstudio_setup_progress) return;
        const started = await api.start_lmstudio_setup() as { success?: boolean; error?: string };
        if (!started?.success) return;
        setProgress({ running: true, done: false, log: [] });
        stopPolling();
        pollRef.current = window.setInterval(async () => {
            const p = await api.get_lmstudio_setup_progress!() as LmSetupProgress;
            setProgress(p ?? {});
            if (p?.done) {
                stopPolling();
                refreshStatus();
            }
        }, 2000);
    };

    const openLmStudioDownload = () => {
        const url = status.lmstudio_download_url || 'https://lmstudio.ai/download';
        if (api?.open_external_url) api.open_external_url(url);
    };

    const loadConnectors = useCallback(async () => {
        if (!api?.get_mcp_servers || !api?.get_native_connectors) return;
        const [srv, native] = await Promise.all([
            api.get_mcp_servers(), api.get_native_connectors(),
        ]) as [MCPServer[], NativeConnector[]];
        setServers(srv ?? []);
        setNativeConnectors(native ?? []);
    }, [api]);

    useEffect(() => { if (step === 'connectors') loadConnectors(); }, [step, loadConnectors]);

    const lmReady = Boolean(status.lms_cli_found && status.server_running && !(status.models_missing?.length));
    const report = progress.report;

    return (
        <div style={styles.overlay}>
            <div style={styles.card}>
                {step === 'welcome' && (
                    <>
                        <div style={styles.header}>
                            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
                                <circle cx="16" cy="16" r="16" fill="var(--chad-accent-active, #7c5cff)" opacity="0.15" />
                                <circle cx="16" cy="16" r="9" fill="var(--chad-accent-active, #7c5cff)" />
                            </svg>
                            <h1 style={styles.title}>Welcome to Chad</h1>
                        </div>
                        <p style={styles.hint}>
                            Chad is an experimental desktop assistant for local models, chat, and optional tools.
                            Start with chat, then enable and configure the capabilities you want to try.
                        </p>
                        <ul style={styles.list}>
                            <li>Runs on local models (LM Studio recommended, or Ollama / any OpenAI-compatible endpoint) — no cloud account required</li>
                            <li>Connects to GitHub, Notion, Todoist, and more with one click</li>
                            <li>Remembers context across conversations</li>
                        </ul>
                        <button style={styles.primaryBtn} onClick={() => setStep('backend-choice')}>
                            Get started
                        </button>
                    </>
                )}

                {step === 'backend-choice' && (
                    <>
                        <div style={styles.header}>
                            <h1 style={styles.title}>Choose your local model backend</h1>
                        </div>
                        <p style={styles.hint}>
                            Choose where model requests go. A server on this computer keeps inference local;
                            remote endpoints and enabled integrations send requests to their configured services.
                        </p>
                        <div style={styles.backendGrid}>
                            <button style={styles.backendCard} onClick={() => setStep('lmstudio')}>
                                <div style={styles.backendCardTitle}>LM Studio <span style={styles.recommendedBadge}>Recommended</span></div>
                                <div style={styles.backendCardHint}>One-click model download &amp; setup, managed for you.</div>
                            </button>
                            <button style={styles.backendCard} onClick={startOllama}>
                                <div style={styles.backendCardTitle}><Server size={14} /> Ollama</div>
                                <div style={styles.backendCardHint}>Already running Ollama? Chad will detect your models.</div>
                            </button>
                            <button style={styles.backendCard} onClick={startCustom}>
                                <div style={styles.backendCardTitle}><Server size={14} /> Custom endpoint</div>
                                <div style={styles.backendCardHint}>Any OpenAI-compatible server (llama.cpp, vLLM, remote).</div>
                            </button>
                        </div>
                    </>
                )}

                {(step === 'ollama' || step === 'custom') && (
                    <>
                        <div style={styles.header}>
                            <h1 style={styles.title}>{step === 'ollama' ? 'Connect Ollama' : 'Connect a custom endpoint'}</h1>
                        </div>
                        <p style={styles.hint}>
                            {step === 'ollama'
                                ? "Chad will look for models already pulled in Ollama — it won't install or pull anything for you."
                                : 'Point Chad at any OpenAI-compatible /v1 endpoint (llama.cpp server, vLLM, a remote box).'}
                        </p>
                        <label style={styles.fieldLabel}>Base URL</label>
                        <input
                            style={styles.textInput}
                            value={customBaseUrl}
                            onChange={e => setCustomBaseUrl(e.target.value)}
                            placeholder="http://localhost:11434/v1"
                        />
                        <label style={styles.fieldLabel}>API key (optional)</label>
                        <input
                            style={styles.textInput}
                            value={customApiKey}
                            onChange={e => setCustomApiKey(e.target.value)}
                            placeholder="leave blank if not required"
                        />
                        <div style={styles.actionsRow}>
                            <button
                                style={styles.secondaryBtn}
                                onClick={() => probeBackend(step === 'ollama' ? 'ollama' : 'custom')}
                                disabled={probing}
                            >
                                {probing ? <Loader2 size={14} className="is-spinning" /> : null} Test connection
                            </button>
                            {step === 'ollama' && !probeResult?.reachable && (
                                <button
                                    style={styles.secondaryBtn}
                                    onClick={() => api?.open_external_url?.('https://ollama.com/download')}
                                >
                                    <ExternalLink size={14} /> Download Ollama
                                </button>
                            )}
                        </div>

                        {probeResult && !probeResult.reachable && (
                            <div style={styles.statusRowErr}><XCircle size={16} /> Couldn't reach that endpoint. Make sure the server is running.</div>
                        )}

                        {probeResult && probeResult.reachable && (probeResult.models?.length ?? 0) > 0 && (
                            <>
                                <label style={styles.fieldLabel}>Model</label>
                                <select
                                    style={styles.textInput}
                                    value={selectedModel}
                                    onChange={e => setSelectedModel(e.target.value)}
                                >
                                    {(probeResult.models ?? []).map(m => (
                                        <option key={m.id} value={m.id}>{m.display_name}</option>
                                    ))}
                                </select>
                                <button
                                    style={styles.primaryBtn}
                                    onClick={() => confirmProviderChoice(step === 'ollama' ? 'ollama' : 'custom')}
                                    disabled={saving || !selectedModel}
                                >
                                    {saving ? <Loader2 size={14} className="is-spinning" /> : null} Use this model
                                </button>
                            </>
                        )}

                        {probeResult && probeResult.reachable && (probeResult.models?.length ?? 0) === 0 && (
                            <div style={styles.hint}>No usable chat models found yet — pull one, then test again.</div>
                        )}

                        {saveError && <div style={styles.errText}>{saveError}</div>}

                        <button style={styles.secondaryBtn} onClick={() => setStep('backend-choice')}>Back</button>
                    </>
                )}

                {step === 'lmstudio' && (
                    <>
                        <div style={styles.header}>
                            <h1 style={styles.title}>Set up local models</h1>
                        </div>
                        <p style={styles.hint}>
                            Chad runs on local models through LM Studio — nothing is sent to the cloud.
                        </p>

                        {!status.lms_cli_found && (
                            <>
                                <p style={styles.hint}>LM Studio isn't installed yet (or hasn't been opened once).</p>
                                <div style={styles.actionsRow}>
                                    <button style={styles.secondaryBtn} onClick={openLmStudioDownload}>
                                        <ExternalLink size={14} /> Download LM Studio
                                    </button>
                                    <button style={styles.primaryBtn} onClick={refreshStatus} disabled={checking}>
                                        {checking ? <Loader2 size={14} className="is-spinning" /> : null} I've installed it — check again
                                    </button>
                                </div>
                            </>
                        )}

                        {status.lms_cli_found && !progress.running && !progress.done && (
                            <>
                                <div style={styles.modelList}>
                                    {(status.required_models ?? []).map(m => (
                                        <div key={m.model_id} style={styles.modelRow}>
                                            <span>{m.model_id}</span>
                                            <span style={styles.modelMeta}>
                                                {status.models_missing?.includes(m.model_id) ? 'not downloaded' : 'ready'} · ~{m.approx_vram_gb}GB · {m.role}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                                {lmReady ? (
                                    <button style={styles.primaryBtn} onClick={() => setStep('connectors')}>Continue</button>
                                ) : (
                                    <button style={styles.primaryBtn} onClick={startLmStudioSetup}>
                                        Download & start
                                    </button>
                                )}
                            </>
                        )}

                        {(progress.running || progress.done) && (
                            <>
                                <div style={styles.log}>
                                    {(progress.log ?? []).map((line, i) => <div key={i}>{line}</div>)}
                                </div>
                                {progress.running && (
                                    <div style={styles.statusRow}><Loader2 size={16} className="is-spinning" /> Setting up…</div>
                                )}
                                {progress.done && report?.ok && (
                                    <>
                                        <div style={styles.statusRowOk}><CheckCircle2 size={16} /> Local models ready</div>
                                        <button style={styles.primaryBtn} onClick={() => setStep('connectors')}>Continue</button>
                                    </>
                                )}
                                {progress.done && !report?.ok && (
                                    <>
                                        <div style={styles.statusRowErr}><XCircle size={16} /> Setup didn't finish cleanly</div>
                                        {(report?.errors ?? []).map((e, i) => <div key={i} style={styles.errText}>{e}</div>)}
                                        <button style={styles.primaryBtn} onClick={startLmStudioSetup}>Try again</button>
                                    </>
                                )}
                            </>
                        )}
                    </>
                )}

                {step === 'connectors' && (
                    <>
                        <div style={styles.header}>
                            <h1 style={styles.title}>Connect your tools</h1>
                        </div>
                        <p style={styles.hint}>
                            Optional — connect any of these now, or skip and do it later from Settings.
                        </p>
                        <div style={styles.connectorsWrap}>
                            <ConnectorsTab servers={servers} nativeConnectors={nativeConnectors} onRefresh={loadConnectors} />
                        </div>
                        <button style={styles.primaryBtn} onClick={onComplete}>Finish</button>
                    </>
                )}
            </div>
        </div>
    );
}

const styles: Record<string, React.CSSProperties> = {
    overlay: {
        position: 'fixed',
        inset: 0,
        background: '#0c0d10',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 3000,
        overflowY: 'auto',
        padding: '32px 0',
    },
    card: {
        width: 'min(480px, 92vw)',
        maxHeight: '90vh',
        overflowY: 'auto',
        background: '#16181d',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 14,
        padding: '32px 28px',
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        color: '#e5e7eb',
    },
    header: {
        display: 'flex',
        alignItems: 'center',
        gap: 12,
    },
    title: {
        margin: 0,
        fontSize: 20,
        fontWeight: 700,
        color: '#f1f3f5',
    },
    hint: {
        margin: 0,
        fontSize: 13,
        color: 'rgba(255,255,255,0.55)',
        lineHeight: 1.5,
    },
    list: {
        margin: 0,
        paddingLeft: 18,
        fontSize: 13,
        color: 'rgba(255,255,255,0.7)',
        lineHeight: 1.8,
    },
    actionsRow: {
        display: 'flex',
        gap: 8,
        flexWrap: 'wrap',
    },
    backendGrid: {
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
    },
    backendCard: {
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        textAlign: 'left',
        padding: '14px 16px',
        borderRadius: 10,
        border: '1px solid rgba(255,255,255,0.1)',
        background: 'rgba(255,255,255,0.03)',
        color: '#e5e7eb',
        cursor: 'pointer',
    },
    backendCardTitle: {
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 14,
        fontWeight: 600,
    },
    backendCardHint: {
        fontSize: 12,
        color: 'rgba(255,255,255,0.5)',
    },
    recommendedBadge: {
        fontSize: 10,
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
        color: 'var(--chad-accent-active, #7c5cff)',
        border: '1px solid var(--chad-accent-active, #7c5cff)',
        borderRadius: 999,
        padding: '2px 8px',
        marginLeft: 6,
    },
    fieldLabel: {
        fontSize: 12,
        fontWeight: 600,
        color: 'rgba(255,255,255,0.6)',
        marginBottom: -6,
    },
    textInput: {
        padding: '9px 12px',
        fontSize: 13,
        borderRadius: 8,
        border: '1px solid rgba(255,255,255,0.12)',
        background: 'rgba(255,255,255,0.04)',
        color: '#e5e7eb',
    },
    modelList: {
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 8,
        padding: 12,
    },
    modelRow: {
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        fontSize: 13,
    },
    modelMeta: {
        fontSize: 11,
        color: 'rgba(255,255,255,0.4)',
    },
    log: {
        maxHeight: 160,
        overflowY: 'auto',
        fontSize: 11,
        fontFamily: 'monospace',
        color: 'rgba(255,255,255,0.5)',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 8,
        padding: 10,
    },
    statusRow: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 13,
    },
    statusRowOk: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 13,
        color: '#4ade80',
    },
    statusRowErr: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 13,
        color: '#f87171',
    },
    errText: {
        fontSize: 12,
        color: '#f87171',
    },
    connectorsWrap: {
        maxHeight: 380,
        overflowY: 'auto',
    },
    primaryBtn: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        padding: '10px 16px',
        fontSize: 14,
        fontWeight: 600,
        borderRadius: 8,
        border: 'none',
        background: 'var(--chad-accent-active, #7c5cff)',
        color: '#fff',
        cursor: 'pointer',
    },
    secondaryBtn: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        padding: '10px 16px',
        fontSize: 14,
        fontWeight: 600,
        borderRadius: 8,
        border: '1px solid rgba(255,255,255,0.12)',
        background: 'transparent',
        color: 'rgba(255,255,255,0.8)',
        cursor: 'pointer',
    },
};
