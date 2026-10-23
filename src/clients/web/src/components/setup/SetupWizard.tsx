/**
 * First-run setup: one chosen Chat model, with recoverable connection checks.
 * Optional specialist models and integrations are configured later in Settings.
 */
import { useEffect, useRef, useState } from 'react';
import { Loader2, ExternalLink, Server, Sparkles, Check, ShieldCheck } from 'lucide-react';
import { useAppStore } from '../../store';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';
import HardwareSetup from './HardwareSetup';
import './SetupWizard.css';

type Step = 'welcome' | 'backend-choice' | 'lmstudio-install' | 'lmstudio' | 'lmstudio-connect' | 'ollama' | 'custom' | 'later';
type Provider = 'lmstudio' | 'ollama' | 'custom';
interface SetupStatus {
    lmstudio_installed?: boolean;
    lms_cli_found?: boolean;
    server_running?: boolean;
    success?: boolean;
    error?: string;
}
interface Model { id: string; display_name: string; supports_tools?: boolean; }
interface Probe { reachable?: boolean; models?: Model[]; }
const DEFAULT_URLS: Record<Provider, string> = {
    lmstudio: 'http://localhost:1234/v1', ollama: 'http://localhost:11434/v1', custom: 'http://localhost:8000/v1',
};

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
    const [step, setStep] = useState<Step>('welcome');
    const [baseUrl, setBaseUrl] = useState(DEFAULT_URLS.lmstudio);
    const [apiKey, setApiKey] = useState('');
    const [probe, setProbe] = useState<Probe | null>(null);
    const [selected, setSelected] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');
    // Ignore late replies after navigating away, editing the endpoint, or unmounting.
    const generation = useRef(0);
    useEffect(() => () => { generation.current++; }, []);
    const api = window.pywebview?.api;
    const provider: Provider = step === 'ollama' ? 'ollama' : step === 'custom' ? 'custom' : 'lmstudio';
    const navigate = (next: Step) => {
        generation.current++;
        setBusy(false); setError(''); setMessage(''); setProbe(null); setSelected('');
        setStep(next);
    };
    const choose = (next: Provider) => {
        setBaseUrl(DEFAULT_URLS[next]); setApiKey(''); navigate(next === 'lmstudio' ? 'lmstudio-install' : next);
    };
    const resetConnection = () => {
        generation.current++; setBusy(false); setProbe(null); setSelected(''); setError(''); setMessage('');
    };
    const check = async () => {
        const request = ++generation.current;
        setBusy(true); setError(''); setMessage(''); setProbe(null); setSelected('');
        try {
            if (!api?.probe_local_backend) throw new Error('Chad is still connecting. Please try again.');
            // Probe HTTP first: CLI discovery must never gate a usable model server.
            const result = await setupRequest(api.probe_local_backend(provider, baseUrl, apiKey)) as Probe;
            if (request !== generation.current) return;
            setProbe(result);
            if (result?.reachable && result.models?.length) {
                setSelected(result.models[0].id);
                setMessage('Connected. Choose a model below.');
            } else if (provider === 'lmstudio') {
                if (!api.get_setup_status) throw new Error('Chad could not check LM Studio. Please try again.');
                const found = await setupRequest(api.get_setup_status()) as SetupStatus;
                if (request !== generation.current) return;
                if (found?.success === false || !found || typeof found !== 'object') {
                    throw new Error('Chad could not check LM Studio. Please try again.');
                }
                setMessage(found.server_running
                    ? 'The default LM Studio server is running. Check the address below and download or load a chat model in LM Studio, then check again.'
                    : found.lmstudio_installed || found.lms_cli_found
                        ? 'LM Studio was found. Open it, download a chat model, then start its Local Server and check again.'
                        : 'No model server responded. If you installed LM Studio, open it, download a chat model and start its Local Server. Chad can connect even if installation detection is unavailable.');
            } else {
                setMessage('No chat models responded at this address. Start the model server, download or load a chat model, then test again.');
            }
        } catch (e) {
            if (request === generation.current) setError(e instanceof Error ? e.message : 'The connection check failed. Please try again.');
        } finally {
            if (request === generation.current) setBusy(false);
        }
    };
    const save = async () => {
        const request = ++generation.current;
        setBusy(true); setError('');
        try {
            if (!api?.save_provider_choice) throw new Error('Chad is still connecting. Please try again.');
            const result = await setupRequest(api.save_provider_choice(provider, baseUrl, apiKey, selected, false)) as { success?: boolean; error?: string };
            if (request !== generation.current) return;
            if (!result?.success) throw new Error(result?.error || 'The model choice could not be saved. Please try again.');
            useAppStore.getState().setActiveModel({
                mode: 'local', name: selected, provider: provider === 'custom' ? 'static' : provider,
                supports_tools: false,
                thinking_enabled: null,
            });
            onComplete();
        } catch (e) {
            if (request === generation.current) setError(e instanceof Error ? e.message : 'The model choice could not be saved.');
        } finally {
            if (request === generation.current) setBusy(false);
        }
    };
    const openDownload = async (url: string) => {
        try {
            if (!api?.open_external_url) throw new Error('The browser could not be opened. Visit ' + url);
            await api.open_external_url(url);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'The download page could not be opened.');
        }
    };
    const connecting = step === 'lmstudio-connect' || step === 'ollama' || step === 'custom';
    const hasModels = probe?.reachable && !!probe.models?.length;

    const stage = step === 'welcome' || step === 'later' ? 0 : step === 'backend-choice' || step === 'lmstudio-install' ? 1 : step === 'lmstudio' ? 2 : 3;
    const heading = useRef<HTMLHeadingElement>(null);
    useEffect(() => {
        const activeHeading = Array.from(document.querySelectorAll<HTMLElement>('.chad-setup h1')).find(element => !element.closest('[hidden]'));
        activeHeading?.focus();
    }, [step]);

    return (
        <div className="chad-setup" style={styles.overlay}>
            <section style={styles.card} aria-label="Chad setup">
                <div className="setup-brand">CHAD · SETUP</div>
                <nav aria-label="Setup progress"><ol className="setup-progress">
                    {['Welcome', 'Model app', 'Choose a model', 'Start chatting'].map((label, index) => <li key={label} aria-current={stage === index ? 'step' : undefined} className={index < stage ? 'is-complete' : ''}>
                        <span className="setup-step-number">{index < stage ? <Check size={14} aria-label="Complete" /> : index + 1}</span>{label}
                    </li>)}
                </ol><p style={{ ...styles.hint, marginTop: 12 }}>Step {stage + 1} of 4</p></nav>
                <div className="setup-stack setup-body">
                {step === 'welcome' && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>Welcome to Chad</h1>
                    <div className="setup-hero-icon"><Sparkles size={30} /></div>
                    <p style={styles.hint}>Let’s get your first conversation ready. We’ll help you find an AI model that suits your computer and connect it to Chad.</p>
                    <div className="setup-note"><ShieldCheck size={18} style={{ verticalAlign: 'middle', marginRight: 8, color: '#c4b5fd' }} />No cloud account needed. With a local model, your chats run on your computer.</div>
                    <button style={styles.primaryBtn} onClick={() => navigate('backend-choice')}>Get started</button>
                </>}
                {step === 'backend-choice' && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>Choose your model app</h1>
                    <p style={styles.hint}>These apps run the AI model that answers your messages.</p>
                    <div style={styles.backendGrid}>
                        <button style={styles.backendCard} onClick={() => choose('lmstudio')}>
                            <span style={styles.backendCardTitle}>LM Studio <span style={styles.recommendedBadge}>Recommended</span></span>
                            <span style={styles.backendCardHint}>Choose and download a model in a desktop app.</span>
                        </button>
                        <details><summary>I already use another model app</summary><div className="setup-details">
                        <button style={styles.backendCard} onClick={() => choose('ollama')}>
                            <span style={styles.backendCardTitle}><Server size={14} /> Ollama</span>
                            <span style={styles.backendCardHint}>Connect to models you have already installed in Ollama.</span>
                        </button>
                        <button style={styles.backendCard} onClick={() => choose('custom')}>
                            <span style={styles.backendCardTitle}>Another model server</span>
                            <span style={styles.backendCardHint}>Connect using an OpenAI-compatible server address.</span>
                        </button>
                        </div></details>
                    </div>
                </>}
                {step === 'lmstudio-install' && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>First, open LM Studio</h1>
                    <p style={styles.hint}>LM Studio is the free app that runs your AI model. You only need to install it once.</p>
                    <div className="setup-note setup-stack">
                        <strong>Already have LM Studio?</strong>
                        <p style={styles.hint}>Open it on this computer, then continue. We’ll help you choose a model next.</p>
                        <button style={styles.secondaryBtn} onClick={() => void openDownload('https://lmstudio.ai/download')}><ExternalLink size={16} /> Download LM Studio</button>
                    </div>
                    <button style={styles.primaryBtn} onClick={() => navigate('lmstudio')}>LM Studio is open — continue</button>
                </>}
                {(step === 'lmstudio' || step === 'lmstudio-connect') && <div hidden={step !== 'lmstudio'}>
                    <HardwareSetup baseUrl={baseUrl} apiKey={apiKey} modelId={selected} onContinue={() => navigate('lmstudio-connect')} />
                </div>}
                {connecting && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>{step === 'lmstudio-connect' ? 'Connect and start chatting' : step === 'ollama' ? 'Connect Ollama' : 'Connect your model server'}</h1>
                    {step === 'lmstudio-connect' ? <>
                        <p style={styles.hint}>One last step in LM Studio, then you’re ready.</p>
                        {!hasModels && <ol className="setup-checklist"><li>Load your downloaded chat model in LM Studio.</li><li>Open <strong>Model Server</strong> (or <strong>Developer</strong>) and turn on the <strong>Local Server</strong>.</li><li>Return here and check the connection.</li></ol>}
                    </> : <p style={styles.hint}>Start your model app’s server, then enter its connection details below.</p>}
                    {step === 'ollama' && <button style={styles.secondaryBtn} onClick={() => void openDownload('https://ollama.com/download')}>Download Ollama</button>}
                    <details open={step !== 'lmstudio-connect'}><summary>Connection settings (advanced)</summary><div className="setup-details">
                    <p style={styles.hint}>The default connects to LM Studio on this computer. Only change this for a different server. A remote address sends your chats there.</p>
                    <label style={styles.fieldLabel} htmlFor="setup-url">Server address</label>
                    <input id="setup-url" style={styles.textInput} value={baseUrl} disabled={busy} onChange={e => { setBaseUrl(e.target.value); resetConnection(); }} />
                    <label style={styles.fieldLabel} htmlFor="setup-key">API key (only if your server requires one)</label>
                    <input id="setup-key" type="password" autoComplete="off" style={styles.textInput} value={apiKey} disabled={busy} onChange={e => { setApiKey(e.target.value); resetConnection(); }} />
                    </div></details>
                    {!hasModels && <button style={styles.primaryBtn} disabled={busy || !baseUrl.trim()} onClick={() => void check()}>
                        {busy && <Loader2 size={14} className="is-spinning" />}
                        {busy ? 'Checking…' : step === 'lmstudio-connect' ? 'Check connection' : 'Test connection'}
                    </button>}
                    {message && <p role="status" className="setup-status" style={styles.hint}>{message}</p>}
                    {!!probe?.models?.length && probe.reachable && <>
                        <label style={styles.fieldLabel} htmlFor="setup-model">Chat model</label>
                        <select id="setup-model" style={styles.textInput} value={selected} disabled={busy} onChange={e => setSelected(e.target.value)}>
                            {probe.models.map(model => <option key={model.id} value={model.id}>{model.display_name}</option>)}
                        </select>
                        <button style={styles.primaryBtn} disabled={busy || !selected} onClick={() => void save()}>Use this model and open Chat</button>
                        <p style={styles.hint}>You can change your model and add optional tools later in Settings.</p>
                    </>}
                </>}
                {step === 'later' && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>You can finish setup later</h1>
                    <p style={styles.hint}>No model has been selected, so Chat is not ready yet. You can close Chad and return to setup next time, or choose a different model app now.</p>
                    <button style={styles.primaryBtn} onClick={() => navigate('backend-choice')}>Continue setup</button>
                </>}
                {error && <p role="alert" style={styles.errText}>{error}</p>}
                </div>
                <div className="setup-footer">
                    {step !== 'welcome' && step !== 'later' && <button style={styles.secondaryBtn} onClick={() => navigate(step === 'backend-choice' ? 'welcome' : step === 'lmstudio-connect' ? 'lmstudio' : step === 'lmstudio' ? 'lmstudio-install' : 'backend-choice')}>Back</button>}
                    {step !== 'later' && <button style={styles.secondaryBtn} onClick={() => navigate('later')}>Finish later</button>}
                </div>
            </section>
        </div>
    );
}
