/** Local desktop assessment; model requests still use the selected backend. */
import { useEffect, useRef, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';
import { Cpu, ArrowRight, Loader2 } from 'lucide-react';
import './SetupWizard.css';

interface Candidate {
    id: string; name: string; quantization: string; weights_gb: number; kv_gb: number;
    required_gb: number; fits: boolean; reasons: string[]; mode: string; source: string;
    speed: { kind: string; low: number | null; high: number | null; note: string };
    settings: { gpu_offload: string; temperature: number; top_p: number; thinking: string; advanced: string };
}
interface Assessment {
    success?: boolean; detail?: string; recommended_id?: string; target_met: boolean;
    hardware: { ram_gb: number | null; available_ram_gb: number | null; cpu: string;
        physical_cores: number | null; storage_free_gb: number | null;
        gpus: { name: string; vram_gb: number | null }[]; warnings: string[] };
    models: Candidate[]; assumptions: string;
}
interface Job {
    running?: boolean; done?: boolean; bytes?: number; total?: number | null; note?: string;
    operation?: string;
    result?: { success?: boolean; detail?: string; note?: string; stats?: {
        tokens_per_second: number; time_to_first_token_seconds: number; model_load_time_seconds?: number;
    }; reasoning?: string };
}
type Pending = { operation: string; options: Record<string, unknown>; summary: string };
const gib = (value: number | null | undefined) => value == null ? 'unknown' : `${value.toFixed(1)} GiB`;
const isLocalServer = (value: string) => {
    try { return ['localhost', '127.0.0.1', '[::1]'].includes(new URL(value).hostname); }
    catch { return false; }
};

export default function HardwareSetup({ baseUrl, apiKey, modelId, onContinue }: { baseUrl: string; apiKey: string; modelId: string; onContinue?: () => void }) {
    const api = window.pywebview?.api;
    const [context, setContext] = useState(8192);
    const [storage, setStorage] = useState('');
    const [manual, setManual] = useState<Record<string, string>>({});
    const [assessment, setAssessment] = useState<Assessment | null>(null);
    const [selected, setSelected] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [pending, setPending] = useState<Pending | null>(null);
    const [job, setJob] = useState<Job>({});
    const confirmation = useRef<HTMLDivElement>(null);
    const generation = useRef(0);
    const candidate = assessment?.models.find(model => model.id === selected);
    useEffect(() => {
        if (pending) {
            confirmation.current?.scrollIntoView?.({ block: 'nearest' });
            confirmation.current?.querySelector('button')?.focus();
        }
    }, [pending]);
    useEffect(() => {
        const timer = window.setInterval(() => {
            if (!api?.get_model_setup_job) return;
            void setupRequest(api.get_model_setup_job()).then(value => setJob(value as Job))
                .catch(() => setError('Setup progress is unavailable. Reopen setup to check the active operation.'));
        }, 1500);
        return () => { generation.current++; window.clearInterval(timer); };
    }, [api]);
    const invalidate = () => { generation.current++; setAssessment(null); setPending(null); setError(''); setBusy(false); };
    const scan = async () => {
        const request = ++generation.current;
        setBusy(true); setError(''); setAssessment(null); setPending(null);
        try {
            if (!api?.assess_model_hardware) throw new Error('Hardware assessment needs the desktop app. You can still choose a model manually.');
            const values = Object.fromEntries(Object.entries(manual).filter(([, value]) => value !== ''));
            const result = await setupRequest(api.assess_model_hardware(storage, context, values), 60000) as Assessment;
            if (request !== generation.current) return;
            if (!result?.success) throw new Error(result?.detail || 'Hardware assessment failed. Try again or choose a model manually.');
            setAssessment(result); setSelected(result.recommended_id || result.models[0]?.id || '');
        } catch (e) {
            if (request === generation.current) setError(e instanceof Error ? e.message : 'Hardware assessment failed.');
        } finally { if (request === generation.current) setBusy(false); }
    };
    const run = async () => {
        if (!pending || !api?.start_model_setup_job) return;
        setError('');
        const action = pending;
        setPending(null);
        try {
            const started = await setupRequest(api.start_model_setup_job(action.operation, action.options, true)) as { success?: boolean; detail?: string };
            if (!started?.success) throw new Error(started?.detail || 'The setup operation could not start.');
            setJob({ running: true, operation: action.operation });
        } catch (e) { setError(e instanceof Error ? e.message : 'Setup operation failed.'); }
    };
    const queue = (operation: string, options: Record<string, unknown>, summary: string) => setPending({ operation, options, summary });
    const continueButton = onContinue && <button style={styles.secondaryBtn} disabled={job.running || !!pending} onClick={onContinue}>{job.done && job.result?.success && job.operation === 'download' ? 'Continue to connection' : 'I already have a model — continue'} <ArrowRight size={16} /></button>;
    return <div className="chad-setup setup-stack">
        <h1 style={styles.title} tabIndex={-1}>Find your first AI model</h1>
        {!assessment && <p style={styles.hint}>A model is the AI that answers your messages. We’ll check your computer and suggest one for smooth, everyday chat.</p>}
        {!assessment && <>
            <div className="setup-note setup-stack"><Cpu size={32} color="#c4b5fd" aria-hidden="true" />
                <strong>No hardware knowledge needed</strong>
                <p style={styles.hint}>Chad checks your memory, graphics card, and free space. The scan stays on this computer and downloads nothing.</p>
            </div>
            <button style={styles.primaryBtn} disabled={busy || job.running} onClick={() => void scan()}>
                {busy ? <><Loader2 size={18} className="is-spinning" /> Checking your computer…</> : 'Find a model for me'}
            </button>
            {continueButton}
        </>}
        {assessment && candidate && <>
            <section className="setup-recommendation" aria-label="Suggested model">
                <span className="setup-badge">{candidate.fits ? candidate.id === assessment.recommended_id ? 'Recommended for your computer' : 'Your selection' : 'Not enough memory right now'}</span>
                <h2>{candidate.name}</h2>
                <p style={styles.hint}>{candidate.fits ? 'A starting point for everyday chat on this computer.' : 'Try a smaller model or free some memory, then scan again.'}</p>
                <div className="setup-metrics">
                    <div><strong>{gib(candidate.weights_gb)}</strong><span>download size</span></div>
                    <div><strong>{candidate.speed.low == null ? 'Needs a speed test' : `${candidate.speed.low}–${candidate.speed.high}`}</strong><span>{candidate.speed.low == null ? 'speed not yet known' : 'estimated tokens / second'}</span></div>
                </div>
                <p style={styles.hint}>{candidate.speed.low == null ? 'We can’t estimate speed reliably on this hardware.' : 'Estimated speed, not a benchmark. Actual speed varies.'}</p>
                {candidate.reasons.map(reason => <p key={reason} role="note" style={styles.errText}>{reason}</p>)}
                <button style={styles.primaryBtn} disabled={job.running || !candidate.fits || !!pending} onClick={() => queue('download', { catalog_id: candidate.id, storage_path: storage }, `Download ${candidate.name} (${gib(candidate.weights_gb)}) to ${storage || 'your default LM Studio model folder'}. You can pause and retry the download.`)}>Download {candidate.name}</button>
            </section>
            {continueButton}
            <details><summary>Compare other models</summary><div className="setup-details setup-alternatives" role="group" aria-label="Model alternatives">
                {assessment.models.map(model => <button className="setup-alternative" key={model.id} aria-pressed={model.id === selected} disabled={job.running || !!pending || !model.fits} onClick={() => { setSelected(model.id); setPending(null); }}>
                    <strong>{model.name}</strong>{model.id === assessment.recommended_id && ' · Recommended'}
                    <small>{gib(model.weights_gb)} download · {model.fits ? model.speed.low == null ? 'Speed needs testing' : `Estimated ${model.speed.low}–${model.speed.high} tokens/sec` : 'Does not fit available memory'}</small>
                </button>)}
            </div></details>
            <details><summary>Suggested LM Studio settings</summary><div className="setup-details">
                <p style={styles.hint}>Use these when loading this model in LM Studio. Chad does not apply them automatically.</p>
                <p style={styles.hint}>Context: {context} tokens · GPU offload: {candidate.settings.gpu_offload}<br />Temperature: {candidate.settings.temperature} · Top-p: {candidate.settings.top_p}<br />Thinking: {candidate.settings.thinking}</p>
                <p style={styles.hint}>{candidate.settings.advanced}</p>
            </div></details>
        </>}
        {pending && <div ref={confirmation} role="group" aria-label="Confirm setup operation" className="setup-note setup-stack">
            <strong>Ready to {pending.operation === 'download' ? 'download this model' : pending.operation}?</strong>
            <p style={styles.hint}>{pending.summary}</p>
            <button style={styles.primaryBtn} onClick={() => void run()}>Confirm {pending.operation}</button>
            <button style={styles.secondaryBtn} onClick={() => setPending(null)}>Cancel</button>
        </div>}
        {job.running && <div role="status" className="setup-status setup-stack">
            <strong>{job.operation === 'download' ? 'Downloading your model' : 'Working on your request'}</strong>
            <p style={styles.hint}>{job.total ? `${gib((job.bytes || 0) / 1024 ** 3)} of ${gib(job.total / 1024 ** 3)}` : job.note || 'This can take a few minutes. You can leave this window open.'}</p>
            <progress value={job.total ? job.bytes || 0 : undefined} max={job.total || undefined} aria-label="Model download progress" />
            {(job.operation === 'download' || job.operation === 'embedding') && <button style={styles.secondaryBtn} onClick={() => void api?.cancel_model_setup_job?.()}>Pause download</button>}
        </div>}
        {job.done && job.result && <div role={job.result.success ? 'status' : 'alert'} className="setup-status setup-stack">
            {job.result.success && <strong>{job.operation === 'download' ? 'Download complete. Load your model in LM Studio next.' : 'Check complete'}</strong>}
            <p style={{ ...styles.hint, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{job.result.detail || job.result.note}</p>
            {job.result.stats && <p style={styles.hint}>Measured {job.result.stats.tokens_per_second.toFixed(1)} tokens/sec · first response {job.result.stats.time_to_first_token_seconds.toFixed(2)}s.</p>}
        </div>}
        {error && <p role="alert" style={styles.errText}>{error}</p>}
        <details><summary>Advanced: scan settings and hardware details</summary><div className="setup-details">
            <label style={styles.fieldLabel} htmlFor="hardware-storage">LM Studio model folder</label>
            <input id="hardware-storage" style={styles.textInput} value={storage} placeholder="Default LM Studio folder" disabled={job.running} onChange={e => { setStorage(e.target.value); invalidate(); }} />
            <p style={styles.hint}>Leave blank for ~/.lmstudio/models. If you moved your models, use the folder shown in LM Studio’s settings. Downloads use this folder after you confirm.</p>
            <label style={styles.fieldLabel} htmlFor="hardware-context">Context tokens</label>
            <select id="hardware-context" style={styles.textInput} value={context} disabled={job.running} onChange={e => { setContext(Number(e.target.value)); invalidate(); }}>
                {[4096, 8192, 16384, 32768].map(size => <option key={size}>{size}</option>)}
            </select>
            <p style={styles.hint}>More context allows longer conversations, but needs more memory. Recommendations aim for 40–50 generated tokens per second or faster, subject to a local speed test.</p>
            {assessment && <>
                <p style={styles.hint}>{assessment.hardware.cpu} · {assessment.hardware.physical_cores ?? 'unknown'} cores<br />RAM {gib(assessment.hardware.ram_gb)} ({gib(assessment.hardware.available_ram_gb)} available)<br />Storage {gib(assessment.hardware.storage_free_gb)} free</p>
                {assessment.hardware.gpus.map((gpu, index) => <p key={index} style={styles.hint}>{gpu.name}: {gib(gpu.vram_gb)} dedicated graphics memory</p>)}
                {assessment.hardware.warnings.map(warning => <p key={warning} style={styles.hint}>{warning}</p>)}
                <p style={styles.hint}>{assessment.assumptions}</p>
                {candidate && <p style={styles.hint}>{candidate.quantization} · KV cache {gib(candidate.kv_gb)} · runtime {gib(candidate.required_gb)} · {candidate.mode === 'gpu' ? 'GPU' : 'CPU'}<br />{candidate.speed.note}</p>}
            </>}
            <details><summary>Correct detected hardware</summary><div className="setup-details">
                <p style={styles.hint}>Leave blank to detect. Graphics memory means one dedicated GPU, not shared memory.</p>
                {(['ram_gb', 'vram_gb', 'storage_free_gb'] as const).map((key, index) => <label key={key} className="setup-stack" style={{ gap: 6 }}>
                    {['Total RAM (GiB)', 'Dedicated VRAM (GiB)', 'Free model storage (GiB)'][index]}
                    <input type="number" min="0" step="0.5" value={manual[key] || ''} disabled={job.running} style={styles.textInput} onChange={e => { setManual(current => ({ ...current, [key]: e.target.value })); invalidate(); }} />
                </label>)}
            </div></details>
            {assessment && <button style={styles.secondaryBtn} disabled={busy || job.running} onClick={() => void scan()}>Scan again</button>}
            <details><summary>Optional memory and search features</summary><div className="setup-details">
                <p style={styles.hint}>Basic chat only needs your chosen model. Optional memory and search use Nomic embeddings on CPU (about 1.8 GiB download and 3 GiB RAM). They use this computer’s cache; a remote Brain needs its own assets.</p>
                <button style={styles.secondaryBtn} disabled={job.running} onClick={() => queue('embedding', {}, 'Download optional Nomic embedding assets (about 1.8 GiB) from Hugging Face into this computer’s cache. Basic chat does not need this download.')}>Download optional embeddings…</button>
            </div></details>
            {modelId && !isLocalServer(baseUrl) && <p style={styles.hint}>This server is remote. Scan and measure speed on that computer instead.</p>}
            {modelId && isLocalServer(baseUrl) && <>
                <button style={styles.secondaryBtn} disabled={job.running} onClick={() => queue('estimate', { model_id: modelId, context, gpu_offload: candidate?.settings.gpu_offload || 'off' }, `Check memory for ${modelId} at ${context} context tokens without loading the model or changing defaults.`)}>Validate memory settings…</button>
                <button style={styles.secondaryBtn} disabled={job.running} onClick={() => queue('benchmark', { model_id: modelId, base_url: baseUrl, api_key: apiKey }, `Generate a short sample with ${modelId}. LM Studio may load it or evict another loaded model. Saved settings will not change.`)}>Measure generation speed…</button>
            </>}
        </div></details>
    </div>;
}
