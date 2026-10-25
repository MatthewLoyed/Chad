/**
 * First-run setup: one chosen Chat model, with recoverable connection checks.
 * Optional specialist models and integrations are configured later in Settings.
 */
import { useEffect, useRef, useState } from 'react';
import { Loader2, ExternalLink, Server, Sparkles, Check, ShieldCheck } from 'lucide-react';
import { useAppStore } from '../../store';
import { setupRequest } from './setupRequest';
import { styles } from './SetupWizard.styles';
import HardwareSetup, { type ModelBundle } from './HardwareSetup';
import './SetupWizard.css';

type Step = 'welcome' | 'backend-choice' | 'lmstudio-install' | 'lmstudio' | 'lmstudio-connect' | 'ollama' | 'custom' | 'cloud' | 'later';
type Provider = 'lmstudio' | 'ollama' | 'custom' | 'cloud';
interface SetupStatus {
    lmstudio_installed?: boolean;
    lms_cli_found?: boolean;
    server_running?: boolean;
    success?: boolean;
    error?: string;
}
interface Model { id: string; display_name: string; supports_tools?: boolean; }
interface Probe { bundle_models?: Record<string, string>; reachable?: boolean; models?: Model[]; detail?: string; success?: boolean; }
const DEFAULT_URLS: Record<Provider, string> = {
    lmstudio: 'http://localhost:1234/v1', ollama: 'http://localhost:11434/v1', custom: 'http://localhost:8000/v1', cloud: 'https://openrouter.ai/api/v1',
};

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
    const [step, setStep] = useState<Step>('welcome');
    const [baseUrl, setBaseUrl] = useState(DEFAULT_URLS.lmstudio);
    const [apiKey, setApiKey] = useState('');
    const [cloudService, setCloudService] = useState(DEFAULT_URLS.cloud);
    const [probe, setProbe] = useState<Probe | null>(null);
    const [selected, setSelected] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');
    const [testReply, setTestReply] = useState('');
    const [bundle, setBundle] = useState<ModelBundle | undefined>();
    const [roleToken, setRoleToken] = useState('');
    const [rolesReady, setRolesReady] = useState(false);
    // Ignore late replies after navigating away, editing the endpoint, or unmounting.
    const generation = useRef(0);
    useEffect(() => () => { generation.current++; }, []);
    const api = window.pywebview?.api;
    const provider: Provider = step === 'ollama' ? 'ollama' : step === 'custom' ? 'custom' : step === 'cloud' ? 'cloud' : 'lmstudio';
    const navigate = (next: Step) => {
        generation.current++;
        setBusy(false); setError(''); setMessage(''); setProbe(null); setSelected(''); setTestReply(''); setRoleToken(''); setRolesReady(false);
        setStep(next);
    };
    const choose = (next: Provider) => {
        setBundle(undefined); setBaseUrl(DEFAULT_URLS[next]); setCloudService(DEFAULT_URLS.cloud); setApiKey(''); navigate(next === 'lmstudio' ? 'lmstudio-install' : next);
    };
    const resetConnection = () => {
        generation.current++; setBusy(false); setProbe(null); setSelected(''); setError(''); setMessage(''); setTestReply(''); setRoleToken(''); setRolesReady(false);
    };
    const check = async () => {
        const request = ++generation.current;
        setBusy(true); setError(''); setMessage(''); setProbe(null); setSelected(''); setTestReply(''); setRoleToken(''); setRolesReady(false);
        try {
            if (!api?.probe_local_backend) throw new Error('Chad is still connecting. Please try again.');
            // Probe HTTP first: CLI discovery must never gate a usable model server.
            const result = await setupRequest(api.probe_local_backend(provider === 'cloud' ? 'custom' : provider, baseUrl, apiKey)) as Probe;
            if (request !== generation.current) return;
            setProbe(result);
            if (result?.reachable && result.models?.length) {
                const preferred = bundle && result.bundle_models?.[bundle.id];
                setSelected(result.models.find(model => model.id === preferred)?.id || (bundle ? preferred || bundle.assets.find(asset => asset.catalog_id === bundle.primary)?.model_key || '' : result.models[0].id));
                setMessage('Model list received. Choose a model, then try a short chat test.');
            } else if (result?.detail) {
                setMessage(result.detail);
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
    const invalidateTest = () => { generation.current++; setBusy(false); setTestReply(''); setRoleToken(''); setRolesReady(false); setError(''); };
    const testChat = async () => {
        const request = ++generation.current;
        setBusy(true); setError(''); setTestReply(''); setRoleToken(''); setRolesReady(false);
        try {
            if (bundle && !api?.test_setup_bundle) throw new Error('The desktop role-check bridge is not ready. Reopen the development app or use manual Chat setup.');
            if (provider === 'cloud' && !baseUrl.startsWith('https://')) throw new Error('Use your cloud provider’s HTTPS API address. A local server can use the existing model server option.');
            if (!api?.test_setup_model) throw new Error('Chad is still connecting. Please try again.');
            const result = await setupRequest(bundle && api.test_setup_bundle
                ? api.test_setup_bundle(bundle.id, baseUrl, apiKey, selected, true)
                : api.test_setup_model(baseUrl, apiKey, selected, true), bundle ? 275000 : 95000) as { success?: boolean; reply?: string; detail?: string; validation_token?: string };
            if (request !== generation.current) return;
            if (result.validation_token && result.reply) {
                setRoleToken(result.validation_token); setRolesReady(!!result.success); setTestReply(result.reply);
                if (!result.success) setError(result.detail || 'A helper model is not ready. Retry or continue with Chat only.');
                return;
            }
            if (request !== generation.current) return;
            if (!result?.success || !result.reply?.trim()) throw new Error(result?.detail || 'The model did not answer. Check its load state, API key and model ID, then retry.');
            setTestReply(result.reply);
        } catch (e) { if (request === generation.current) setError(e instanceof Error ? e.message : 'The chat test could not finish.'); }
        finally { if (request === generation.current) setBusy(false); }
    };
    const save = async (chatOnly = false) => {
        if (!testReply) return;
        const request = ++generation.current;
        setBusy(true); setError('');
        try {
            if (!api?.save_provider_choice || (bundle && (!roleToken || !api.save_setup_bundle))) throw new Error('Chad is still connecting. Test the selected setup again before saving.');
            const result = await setupRequest(bundle && roleToken && api.save_setup_bundle
                ? api.save_setup_bundle(baseUrl, apiKey, selected, roleToken, chatOnly)
                : api.save_provider_choice(provider, baseUrl, apiKey, selected, false)) as { success?: boolean; error?: string; detail?: string };
            if (request !== generation.current) return;
            if (!result?.success) throw new Error(result?.detail || result?.error || 'The model choice could not be saved. Please try again.');
            useAppStore.getState().setActiveModel({
                mode: 'local', name: selected, provider: provider === 'custom' || provider === 'cloud' ? 'static' : provider,
                hosting: provider === 'cloud' ? 'cloud' : 'unknown',
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
    const connecting = step === 'lmstudio-connect' || step === 'ollama' || step === 'custom' || step === 'cloud';
    const hasModels = probe?.reachable && !!probe.models?.length;

    const stage = step === 'welcome' || step === 'later' ? 0 : step === 'backend-choice' || step === 'lmstudio-install' ? 1 : step === 'lmstudio' || (step === 'cloud' && !testReply) ? 2 : 3;
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
                    {['Welcome', 'Connection', 'Choose a model', 'Start chatting'].map((label, index) => <li key={label} aria-current={stage === index ? 'step' : undefined} className={index < stage ? 'is-complete' : ''}>
                        <span className="setup-step-number">{index < stage ? <Check size={14} aria-label="Complete" /> : index + 1}</span>{label}
                    </li>)}
                </ol><p style={{ ...styles.hint, marginTop: 12 }}>Step {stage + 1} of 4</p></nav>
                <div className="setup-stack setup-body">
                {step === 'welcome' && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>Welcome to Chad</h1>
                    <div className="setup-hero-icon"><Sparkles size={30} /></div>
                    <p style={styles.hint}>Let’s get your first conversation ready. Choose a model on your computer or connect a cloud service. We’ll guide you to your first answer.</p>
                    <div className="setup-note"><ShieldCheck size={18} style={{ verticalAlign: 'middle', marginRight: 8, color: '#c4b5fd' }} />No cloud account needed. With a local model, your chats run on your computer.</div>
                    <button style={styles.primaryBtn} onClick={() => navigate('backend-choice')}>Get started</button>
                </>}
                {step === 'backend-choice' && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>Where should your AI run?</h1>
                    <p style={styles.hint}>Use this computer for local chat, or bring an API key for a cloud service.</p>
                    <div style={styles.backendGrid}>
                        <button style={styles.backendCard} onClick={() => choose('lmstudio')}>
                            <span style={styles.backendCardTitle}>LM Studio <span style={styles.recommendedBadge}>Recommended</span></span>
                            <span style={styles.backendCardHint}>Choose and download a model in a desktop app.</span>
                        </button>
                        <button style={styles.backendCard} onClick={() => choose('cloud')}>
                            <span style={styles.backendCardTitle}>Use a cloud API</span>
                            <span style={styles.backendCardHint}>No local model download. Requires a provider account and API key; usage may cost money.</span>
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
                    <HardwareSetup baseUrl={baseUrl} apiKey={apiKey} modelId={selected} onContinue={plan => { setBundle(plan); navigate('lmstudio-connect'); }} />
                </div>}
                {connecting && <>
                    <h1 ref={heading} tabIndex={-1} style={styles.title}>{step === 'lmstudio-connect' ? 'Connect and start chatting' : step === 'ollama' ? 'Connect Ollama' : step === 'cloud' ? 'Connect your cloud model' : 'Connect your model server'}</h1>
                    {step === 'lmstudio-connect' ? <>
                        <p style={styles.hint}>One last step in LM Studio, then you’re ready.</p>
                        {!hasModels && <ol className="setup-checklist"><li>Load your downloaded chat model in LM Studio.</li><li>Open <strong>Model Server</strong> (or <strong>Developer</strong>) and turn on the <strong>Local Server</strong>.</li><li>Return here and check the connection.</li></ol>}
                    </> : <p style={styles.hint}>{step === 'cloud' ? 'Choose a provider and bring its API key. No local model download is needed.' : 'Start your model app’s server, then enter its connection details below.'}</p>}
                    {step === 'ollama' && <button style={styles.secondaryBtn} onClick={() => void openDownload('https://ollama.com/download')}>Download Ollama</button>}
                    {step === 'cloud' && <div className="setup-stack">
                        <p style={styles.hint}>Your messages go to this provider and usage may cost money. API billing can be separate from a chat subscription.</p>
                        <label style={styles.fieldLabel} htmlFor="setup-cloud-service">Cloud service</label>
                        <select id="setup-cloud-service" style={styles.textInput} value={cloudService} disabled={busy} onChange={e => { setCloudService(e.target.value); setBaseUrl(e.target.value); setApiKey(''); resetConnection(); }}>
                            <option value="https://openrouter.ai/api/v1">OpenRouter</option>
                            <option value="https://api.deepseek.com/v1">DeepSeek</option>
                            <option value="">Another compatible API</option>
                        </select>
                        <label style={styles.fieldLabel} htmlFor="setup-cloud-key">Provider API key</label>
                        <input id="setup-cloud-key" type="password" autoComplete="off" style={styles.textInput} value={apiKey} disabled={busy} onChange={e => { setApiKey(e.target.value); resetConnection(); }} />
                    </div>}
                    <details open={step === 'ollama' || step === 'custom' || (step === 'cloud' && !cloudService)}><summary>{step === 'cloud' ? 'API address and compatibility' : 'Connection settings (advanced)'}</summary><div className="setup-details">
                    <p style={styles.hint}>{step === 'lmstudio-connect' ? 'The default connects to LM Studio on this computer.' : 'Use the provider’s API base address, not its chat website or a /chat/completions URL.'} A remote address sends your chats there.</p>
                    <label style={styles.fieldLabel} htmlFor="setup-url">{step === 'cloud' ? 'API base address' : 'Server address'}</label>
                    <input id="setup-url" style={styles.textInput} value={baseUrl} disabled={busy} onChange={e => { setBaseUrl(e.target.value); setApiKey(''); if (step === 'cloud') setCloudService(''); resetConnection(); }} />
                    {step !== 'cloud' && <><label style={styles.fieldLabel} htmlFor="setup-key">API key (only if your server requires one)</label>
                    <input id="setup-key" type="password" autoComplete="off" style={styles.textInput} value={apiKey} disabled={busy} onChange={e => { setApiKey(e.target.value); resetConnection(); }} />
                    </>}
                    {step === 'cloud' && <p style={styles.hint}>This setup uses OpenAI-compatible Chat Completions. Native Anthropic Messages, Gemini and Responses API setup is not available here yet. A model name does not establish the API format.</p>}
                    </div></details>
                    {!hasModels && <button style={step === 'cloud' ? styles.secondaryBtn : styles.primaryBtn} disabled={busy || !baseUrl.trim()} onClick={() => void check()}>
                        {busy && <Loader2 size={14} className="is-spinning" />}
                        {busy ? 'Checking…' : step === 'lmstudio-connect' ? 'Check connection' : 'Find available models'}
                    </button>}
                    {message && <p role="status" className="setup-status" style={styles.hint}>{message}</p>}
                    {hasModels && <>
                        <label style={styles.fieldLabel} htmlFor="setup-model">Chat model</label>
                        <select id="setup-model" style={styles.textInput} value={selected} disabled={busy} onChange={e => { setSelected(e.target.value); setBundle(undefined); invalidateTest(); }}>
                            {probe?.models?.map(model => <option key={model.id} value={model.id}>{model.display_name}</option>)}
                        </select>
                    </>}
                    {(provider === 'cloud' || provider === 'custom' || (probe && !hasModels)) && <>
                        <label style={styles.fieldLabel} htmlFor="setup-model-id">Model ID (from your model app or provider)</label>
                        <input id="setup-model-id" style={styles.textInput} value={selected} disabled={busy} placeholder="Exact model ID" onChange={e => { setSelected(e.target.value); setBundle(undefined); invalidateTest(); }} />
                        <p style={styles.hint}>Some providers do not list models. You can enter an ID and test it directly.</p>
                    </>}
                    {bundle && <div className="setup-note setup-stack"><strong>Recommended model roles selected</strong><p style={styles.hint}>Main, fast secondary and tool selection will be checked separately. Subagents inherit your main/secondary models.</p><button style={styles.secondaryBtn} onClick={() => { setBundle(undefined); invalidateTest(); }}>Use manual Chat setup instead</button></div>}
                    {selected && <>
                        {!testReply ? <>
                            <p style={styles.hint}>{bundle ? 'Check the main, secondary and tool-selector roles. Models may load or be evicted. The selector checks a fictional tool list; no real tools run.' : 'Try one short greeting with this model. It may load a local model; a hosted service may charge for this request. No tools will run.'}</p>
                            <button style={styles.primaryBtn} disabled={busy || !baseUrl.trim()} onClick={() => void testChat()}>{busy ? 'Testing…' : bundle ? 'Test recommended setup' : 'Send a short test message'}</button>
                        </> : <>
                            <div className="setup-status setup-stack" role="status"><strong>Your model answered</strong><p style={styles.hint}>{testReply}</p></div>
                            {(!bundle || rolesReady) && <button style={styles.primaryBtn} disabled={busy} onClick={() => void save()}>Use this model and open Chat</button>}
                            {bundle && !rolesReady && <>
                                <button style={styles.primaryBtn} disabled={busy} onClick={() => void testChat()}>Retry model-role checks</button>
                                <button style={styles.secondaryBtn} disabled={busy} onClick={() => void save(true)}>Continue with Chat only</button>
                            </>}
                        </>}
                        <p style={styles.hint}>Chat setup does not verify tool calling, images or reasoning modes. You can change models and configure those features later.</p>
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
