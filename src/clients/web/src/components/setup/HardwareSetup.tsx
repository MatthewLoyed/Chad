/** Local desktop assessment; model requests still use the selected backend. */
import { useEffect, useRef, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';

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

export default function HardwareSetup({ baseUrl, apiKey, modelId }: { baseUrl: string; apiKey: string; modelId: string }) {
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
    const generation = useRef(0);
    const candidate = assessment?.models.find(model => model.id === selected);
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
    return <details style={styles.modelList}>
        <summary>Find models for this computer</summary>
        <p style={styles.hint}>Aim for the best quality around 40–50 generated tokens/sec or faster. Scans stay on this desktop; remote servers need their own assessment.</p>
        <label style={styles.fieldLabel} htmlFor="hardware-storage">LM Studio model folder (blank uses ~/.lmstudio/models)</label>
        <input id="hardware-storage" style={styles.textInput} value={storage} disabled={job.running} onChange={e => { setStorage(e.target.value); invalidate(); }} />
        <p style={styles.hint}>Use the folder shown in LM Studio's model-directory settings. Chad checks that drive and downloads there only after confirmation.</p>
        <label style={styles.fieldLabel} htmlFor="hardware-context">Context tokens</label>
        <select id="hardware-context" style={styles.textInput} value={context} disabled={job.running} onChange={e => { setContext(Number(e.target.value)); invalidate(); }}>
            {[4096, 8192, 16384, 32768].map(size => <option key={size}>{size}</option>)}
        </select>
        <details><summary>Correct detected hardware</summary>
            <p style={styles.hint}>Replace capacity for a what-if estimate. Leave blank to detect. VRAM describes one dedicated GPU, never shared memory.</p>
            {(['ram_gb', 'vram_gb', 'storage_free_gb'] as const).map((key, index) => <label key={key} style={styles.modelRow}>
                {['Total RAM (GiB)', 'Dedicated VRAM (GiB)', 'Free model storage (GiB)'][index]}
                <input type="number" min="0" step="0.5" value={manual[key] || ''} disabled={job.running} style={styles.textInput}
                    onChange={e => { setManual(current => ({ ...current, [key]: e.target.value })); invalidate(); }} />
            </label>)}
        </details>
        <button style={styles.secondaryBtn} disabled={busy || job.running} onClick={() => void scan()}>{busy ? 'Scanning…' : 'Scan and recommend'}</button>
        {assessment && <>
            <p style={styles.hint}>{assessment.hardware.cpu} · {assessment.hardware.physical_cores ?? 'unknown'} cores · RAM {gib(assessment.hardware.ram_gb)} ({gib(assessment.hardware.available_ram_gb)} available) · storage {gib(assessment.hardware.storage_free_gb)} free</p>
            {assessment.hardware.gpus.map((gpu, index) => <p key={index} style={styles.hint}>{gpu.name}: {gib(gpu.vram_gb)} dedicated VRAM</p>)}
            {assessment.hardware.warnings.map(warning => <p key={warning} style={styles.hint}>{warning}</p>)}
            {!assessment.target_met && <p style={styles.hint}>The speed target is unverified on this computer. Compatible fallbacks are shown; benchmark before relying on a token rate.</p>}
            <label htmlFor="hardware-model" style={styles.fieldLabel}>Compare suggested models</label>
            <select id="hardware-model" style={styles.textInput} value={selected} disabled={job.running} onChange={e => { setSelected(e.target.value); setPending(null); }}>
                {assessment.models.map(model => <option key={model.id} value={model.id}>{model.name}{model.id === assessment.recommended_id ? ' — Recommended' : ''}{!model.fits ? ' — Does not fit' : ''}</option>)}
            </select>
            {candidate && <>
                <strong>{candidate.name} · {candidate.quantization}</strong>
                <p style={styles.hint}>Download {gib(candidate.weights_gb)} · KV cache {gib(candidate.kv_gb)} · model/runtime {gib(candidate.required_gb)} · {candidate.mode === 'gpu' ? 'GPU placement' : 'CPU fallback'}</p>
                <p style={styles.hint}>{candidate.speed.low == null ? 'Speed unknown' : `Estimated ${candidate.speed.low}–${candidate.speed.high} tokens/sec`}. {candidate.speed.note}</p>
                {candidate.reasons.map(reason => <p key={reason} style={styles.errText}>{reason}</p>)}
                <p style={styles.hint}>Suggested load: {context} context tokens, GPU offload {candidate.settings.gpu_offload}. Temperature {candidate.settings.temperature}, top-p {candidate.settings.top_p}. Thinking: {candidate.settings.thinking}.</p>
                <p style={styles.hint}>{candidate.settings.advanced} These suggestions do not change LM Studio settings.</p>
                <button style={styles.secondaryBtn} disabled={job.running || !candidate.fits} onClick={() => queue('download', { catalog_id: candidate.id, storage_path: storage }, `Download ${candidate.name} ${candidate.quantization} (${gib(candidate.weights_gb)}) into the model folder above. Retry reuses partial data.`)}>Download this model…</button>
            </>}
            <details><summary>Memory assumptions</summary><p style={styles.hint}>{assessment.assumptions}</p></details>
        </>}
        <details><summary>Internal models and optional assets</summary>
            <p style={styles.hint}>Basic Chat needs only your chosen model. Setup shares it with the tool selector and compatible subagents, preserves explicit specialist overrides, and disables the extra intent-router model. Optional specialist downloads are configured later in Settings.</p>
            <p style={styles.hint}>Memory and semantic tool search use nomic-ai/nomic-embed-text-v2-moe on CPU (about 1.8 GiB download; 3 GiB RAM reserved). Without cached assets they remain unavailable; basic Chat still works. Downloads populate this desktop's cache. A remote Brain needs its own cached assets. Network failure keeps completed cache files for retry. No separate LM Studio embedding model is needed.</p>
            <button style={styles.secondaryBtn} disabled={job.running} onClick={() => queue('embedding', {}, 'Download optional Nomic embedding assets (about 1.8 GiB) from Hugging Face into its local cache. This enables memory and semantic search; it is not needed for basic Chat.')}>Download optional embeddings…</button>
        </details>
        {modelId && !isLocalServer(baseUrl) && <p style={styles.hint}>This model server is remote. Run its memory and speed checks on that host; this desktop's scan does not establish remote fit.</p>}
        {modelId && isLocalServer(baseUrl) && <>
            <p style={styles.hint}>Validate the selected downloaded Chat model: {modelId}. Native memory estimates require the local lms CLI. Benchmarking requires the local LM Studio native API.</p>
            <button style={styles.secondaryBtn} disabled={job.running} onClick={() => queue('estimate', { model_id: modelId, context, gpu_offload: candidate?.settings.gpu_offload || 'off' }, `Run an estimate-only memory check for ${modelId} at ${context} context tokens. This does not load a model or change defaults.`)}>Validate memory settings…</button>
            <button style={styles.secondaryBtn} disabled={job.running} onClick={() => queue('benchmark', { model_id: modelId, base_url: baseUrl, api_key: apiKey }, `Generate one short sample with ${modelId}. LM Studio may load it or evict another resident model. Saved settings will not change. This measures generation speed, not tool accuracy.`)}>Measure generation speed…</button>
        </>}
        {pending && <div role="group" aria-label="Confirm setup operation" style={styles.modelList}>
            <p style={styles.hint}>{pending.summary}</p>
            <button style={styles.primaryBtn} onClick={() => void run()}>Confirm {pending.operation}</button>
            <button style={styles.secondaryBtn} onClick={() => setPending(null)}>Cancel</button>
        </div>}
        {job.running && <div role="status">
            <p style={styles.hint}>Working: {job.operation}. {job.total ? `${gib((job.bytes || 0) / 1024 ** 3)} / ${gib(job.total / 1024 ** 3)}` : job.note || 'Waiting for the local operation…'}</p>
            {!!job.total && <progress value={job.bytes || 0} max={job.total} aria-label="Model download progress" />}
            {(job.operation === 'download' || job.operation === 'embedding') && <button style={styles.secondaryBtn} onClick={() => void api?.cancel_model_setup_job?.()}>Pause download</button>}
        </div>}
        {job.done && job.result && <div role={job.result.success ? 'status' : 'alert'}>
            <p style={{ ...styles.hint, whiteSpace: 'pre-wrap' }}>{job.result.detail || job.result.note}</p>
            {job.result.stats && <p style={styles.hint}>Measured {job.result.stats.tokens_per_second.toFixed(1)} tokens/sec · first token {job.result.stats.time_to_first_token_seconds.toFixed(2)}s · load {job.result.stats.model_load_time_seconds?.toFixed(2) ?? 'already loaded'}s · reasoning {job.result.reasoning}.</p>}
        </div>}
        {error && <p role="alert" style={styles.errText}>{error}</p>}
    </details>;
}
