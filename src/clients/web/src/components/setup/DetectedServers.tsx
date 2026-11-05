import { useEffect, useState } from 'react';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';

export interface DetectedServer {
    provider: 'lmstudio' | 'ollama'; label: string; base_url: string;
    reachable: boolean; models: { id: string; display_name: string }[]; detail?: string;
}

export default function DetectedServers({ onChoose }: { onChoose: (server: DetectedServer) => void }) {
    const [servers, setServers] = useState<DetectedServer[]>([]);
    const [checking, setChecking] = useState(false);
    const [generation, setGeneration] = useState(0);
    const [error, setError] = useState('');
    const api = window.pywebview?.api;
    useEffect(() => {
        let active = true;
        if (!api?.discover_setup_servers) return;
        setChecking(true); setError('');
        setupRequest(api.discover_setup_servers(), 30000).then(raw => {
            if (active) setServers((raw as { servers?: DetectedServer[] }).servers?.filter(server => server.reachable) || []);
        }).catch(() => { if (active) setError('Discovery could not finish. Choose a connection below or retry.'); })
            .finally(() => { if (active) setChecking(false); });
        return () => { active = false; };
    }, [api, generation]);
    if (!api?.discover_setup_servers) return null;
    return <section className="setup-stack" aria-label="Detected model servers">
        <p style={styles.hint}>Checking known local addresses on the Brain’s computer. A remote Brain checks its own computer. No models are loaded or downloaded.</p>
        {checking && <p role="status">Looking for LM Studio and Ollama…</p>}
        {servers.map(server => <button key={server.provider} style={styles.backendCard} onClick={() => onChoose(server)}>
            <strong>{server.label} found</strong>
            <span>{server.models.length ? `${server.models.length} chat models available` : server.detail || 'Server found; no chat models listed yet'}</span>
        </button>)}
        {error && <p role="status">{error}</p>}
        <button style={styles.secondaryBtn} disabled={checking} onClick={() => setGeneration(value => value + 1)}>Look again</button>
    </section>;
}
