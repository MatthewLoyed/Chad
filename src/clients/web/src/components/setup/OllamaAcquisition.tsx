import { useEffect, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';

interface Choice { model: string; name: string; size_bytes: number; fits: boolean; settings_note: string }
export default function OllamaAcquisition({ baseUrl, apiKey, onReady }: { baseUrl: string; apiKey: string; onReady: () => void }) {
    const api = window.pywebview?.api;
    const [choices, setChoices] = useState<Choice[]>([]);
    const [pending, setPending] = useState<Choice | null>(null);
    const [running, setRunning] = useState(false);
    const [note, setNote] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        if (!running || !api?.get_model_setup_job) return;
        let active = true;
        const timer = window.setInterval(() => {
            setupRequest(api.get_model_setup_job()).then(raw => {
                const result = raw as { done?: boolean; note?: string; result?: { success?: boolean; note?: string; detail?: string } };
                if (!active) return;
                setNote(result.result?.note || result.result?.detail || result.note || 'Ollama is preparing your model…');
                if (result.done) { setRunning(false); if (result.result?.success) onReady(); }
            }).catch(() => { if (active) { setRunning(false); setNote('Progress unavailable. Check Ollama before retrying.'); } });
        }, 1500);
        return () => { active = false; window.clearInterval(timer); };
    }, [api, running, onReady]);
    let local = false;
    try { local = ['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseUrl).hostname); } catch { /* address edited */ }
    if (!api?.assess_ollama_hardware || !local) return null;
    const scan = async () => {
        setBusy(true); setNote('');
        try {
            const result = await setupRequest(api.assess_ollama_hardware(), 60000) as { models?: Choice[] };
            setChoices(result.models?.filter(model => model.fits) || []);
            if (!result.models?.some(model => model.fits)) setNote('No curated Ollama model fits the detected budget. Free memory or use another server.');
        } catch { setNote('Hardware assessment unavailable. Download a model in Ollama manually.'); }
        finally { setBusy(false); }
    };
    const pull = async () => {
        if (!pending || !api?.start_model_setup_job) return;
        setBusy(true);
        try {
            const result = await setupRequest(api.start_model_setup_job('ollama-pull', { model_id: pending.model, base_url: baseUrl, api_key: apiKey }, true)) as { success?: boolean; detail?: string };
            setPending(null); setRunning(!!result.success); setNote(result.detail || '');
        } catch { setNote('The download could not start. Check Ollama, then retry.'); }
        finally { setBusy(false); }
    };
    return <details><summary>Need an Ollama model?</summary><div className="setup-details setup-stack">
        <p style={styles.hint}>Assess this computer for a curated Ollama download. Remote servers need their own hardware assessment. Ollama controls its model folder and storage.</p>
        <button style={styles.secondaryBtn} disabled={busy || running} onClick={() => void scan()}>Find an Ollama model for me</button>
        {choices.map(choice => <button key={choice.model} style={styles.secondaryBtn} disabled={busy || running} onClick={() => setPending(choice)}>
            Download {choice.name} · {(choice.size_bytes / 1024 ** 3).toFixed(1)} GiB
        </button>)}
        {pending && <div role="group" aria-label="Confirm Ollama download">
            <p>{pending.settings_note}</p><p>The selected Ollama server will download and verify {pending.model}. Its files differ from LM Studio’s curated bundle.</p>
            <button style={styles.primaryBtn} disabled={busy} onClick={() => void pull()}>Confirm Ollama download</button>
            <button style={styles.secondaryBtn} disabled={busy} onClick={() => setPending(null)}>Cancel</button>
        </div>}
        {running && <button style={styles.secondaryBtn} onClick={() => { void api?.cancel_model_setup_job?.(); }}>Stop monitoring pull</button>}
        {note && <p role="status" style={styles.hint}>{note}</p>}
    </div></details>;
}
