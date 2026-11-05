import { useEffect, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';

interface Result { success?: boolean; detail?: string; note?: string; metric?: string; stats?: { tokens_per_second: number } }
export default function ProviderSpeedTest({ provider, baseUrl, apiKey, modelId }: { provider: string; baseUrl: string; apiKey: string; modelId: string }) {
    const api = window.pywebview?.api;
    const [confirm, setConfirm] = useState(false);
    const [running, setRunning] = useState(false);
    const [result, setResult] = useState<Result | null>(null);
    const [error, setError] = useState('');
    useEffect(() => {
        if (!running || !api?.get_model_setup_job) return;
        let active = true;
        const timer = window.setInterval(() => {
            setupRequest(api.get_model_setup_job()).then(raw => {
                const job = raw as { done?: boolean; result?: Result };
                if (active && job.done) { setRunning(false); setResult(job.result || null); }
            }).catch(() => { if (active) { setRunning(false); setError('Measurement progress is unavailable. Check the server before retrying.'); } });
        }, 1500);
        return () => { active = false; window.clearInterval(timer); };
    }, [api, running]);
    let local = false;
    try { local = ['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseUrl).hostname); } catch { /* address edited */ }
    if (!local || provider === 'cloud' || !api?.start_model_setup_job) return null;
    const start = async () => {
        setConfirm(false); setError(''); setResult(null);
        try {
            const job = await setupRequest(api.start_model_setup_job('provider-benchmark', {
                provider, base_url: baseUrl, api_key: apiKey, model_id: modelId,
            }, true)) as { success?: boolean; detail?: string };
            if (!job.success) setError(job.detail || 'The speed test could not start.');
            else setRunning(true);
        } catch { setError('The speed test could not start.'); }
    };
    return <details><summary>Measure speed on this computer</summary><div className="setup-details setup-stack">
        <p style={styles.hint}>Optional short text generation using this desktop’s local endpoint. It may load a model or evict another one. No tools run and saved settings are preserved.</p>
        <button style={styles.secondaryBtn} disabled={running} onClick={() => setConfirm(true)}>Measure generation speed…</button>
        {confirm && <div role="group" aria-label="Confirm speed test"><p>Generate a short sample with {modelId} using the server’s current settings?</p>
            <button style={styles.primaryBtn} onClick={() => void start()}>Confirm speed test</button>
            <button style={styles.secondaryBtn} onClick={() => setConfirm(false)}>Cancel</button>
        </div>}
        {running && <p role="status">Measuring…</p>}
        {result?.stats && <p role="status">Measured {result.stats.tokens_per_second.toFixed(1)} tokens/sec · {result.metric === 'provider_decode' ? 'provider generation speed' : 'full request throughput'}</p>}
        {result && <p style={styles.hint}>{result.note || result.detail}</p>}
        {error && <p role="alert">{error}</p>}
    </div></details>;
}
