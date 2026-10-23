import { useCallback, useEffect, useState } from 'react';
import { Cloud, Cpu, Loader2, RefreshCw, TriangleAlert, Check } from 'lucide-react';
import { useAppStore } from '../../../store';
import type { ActiveModelInfo, LocalModelInfo } from '../../../store';
import { useApiReady } from '../../../store';
import './ModelsTab.css';

const DISCOVERY_TIMEOUT_MS = 12000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
        promise.then(
            (v) => { clearTimeout(timer); resolve(v); },
            (e) => { clearTimeout(timer); reject(e); },
        );
    });
}

function shortModelName(name: string): string {
    return name.split('/').pop()?.replace(/-/g, ' ') ?? name;
}

export default function ModelsTab() {
    const apiReady = useApiReady();
    const activeModel = useAppStore((s) => s.activeModel);
    const setActiveModel = useAppStore((s) => s.setActiveModel);
    const cloudModels = useAppStore((s) => s.availableCloudModels);
    const setAvailableCloudModels = useAppStore((s) => s.setAvailableCloudModels);
    const localModels = useAppStore((s) => s.availableLocalModels) as LocalModelInfo[];
    const setAvailableLocalModels = useAppStore((s) => s.setAvailableLocalModels);
    const isLocalScanning = useAppStore((s) => s.isLocalScanning);
    const setIsLocalScanning = useAppStore((s) => s.setIsLocalScanning);

    const [switching, setSwitching] = useState(false);
    const [bootstrapped, setBootstrapped] = useState(false);

    const api = apiReady ? window.pywebview?.api : undefined;

    const refreshLocal = useCallback(async () => {
        if (!api?.discover_local_models) return;
        if (localModels.length === 0) {
            setIsLocalScanning(true);
        }
        try {
            const models = await withTimeout(
                api.discover_local_models(),
                DISCOVERY_TIMEOUT_MS,
                'discover_local_models',
            );
            if (Array.isArray(models)) {
                setAvailableLocalModels(models);
            }
        } catch (e) {
            console.warn('[ModelsTab] discover_local_models failed:', e);
        } finally {
            setIsLocalScanning(false);
        }
    }, [api, localModels.length, setAvailableLocalModels, setIsLocalScanning]);

    useEffect(() => {
        if (!apiReady || bootstrapped) return;
        setBootstrapped(true);

        (async () => {
            try {
                if (api?.get_active_model_info) {
                    const info = await api.get_active_model_info() as ActiveModelInfo | null;
                    if (info) setActiveModel(info);
                }
                if (api?.get_available_models) {
                    const models = await api.get_available_models();
                    if (Array.isArray(models)) {
                        setAvailableCloudModels(models as { id: string; name: string }[]);
                    }
                }
                await refreshLocal();
            } catch (e) {
                console.error('[ModelsTab] bootstrap failed:', e);
            }
        })();
    }, [api, apiReady, bootstrapped, refreshLocal, setActiveModel, setAvailableCloudModels]);

    const selectModel = async (payload: LocalModelInfo | { mode: 'cloud'; id: string; name: string }) => {
        if (!api?.set_active_model || switching) return;
        setSwitching(true);
        try {
            const modelInfo = 'mode' in payload && payload.mode === 'cloud'
                ? { mode: 'cloud', name: payload.id, display_name: payload.name }
                : payload;

            const success = await api.set_active_model(modelInfo);
            if (!success) return;

            if ('mode' in payload && payload.mode === 'cloud') {
                setActiveModel({
                    mode: 'cloud',
                    name: payload.name,
                    provider: 'cloud',
                    supports_tools: true,
                    thinking_enabled: null,
                    model_settings: {},
                });
            } else {
                const info = typeof api.get_active_model_info === 'function'
                    ? await api.get_active_model_info() as ActiveModelInfo | null
                    : null;
                if (info) {
                    setActiveModel(info);
                } else {
                    const m = payload as LocalModelInfo;
                    setActiveModel({
                        mode: 'local',
                        name: m.name,
                        provider: m.provider,
                        supports_tools: m.supports_tools,
                        thinking_enabled: null,
                        model_settings: {},
                    });
                }
            }
        } catch (e) {
            console.error('[ModelsTab] set_active_model failed:', e);
        } finally {
            setSwitching(false);
        }
    };

    const isCloudActive = (id: string, name: string) =>
        activeModel?.mode === 'cloud' && (activeModel.name === id || activeModel.name === name);

    const isLocalActive = (name: string) =>
        activeModel?.mode === 'local' && activeModel.name === name;

    return (
        <div className="models-tab">
            {activeModel && (
                <div className={`models-tab__active ${activeModel.mode}`}>
                    <div className="models-tab__active-icon">
                        {activeModel.mode === 'cloud' ? <Cloud size={18} /> : <Cpu size={18} />}
                    </div>
                    <div className="models-tab__active-text">
                        <span className="models-tab__active-label">Active model</span>
                        <span className="models-tab__active-name">{activeModel.name}</span>
                    </div>
                    {switching && <Loader2 size={16} className="models-tab__spin" />}
                </div>
            )}

            <p className="models-tab__hint">
                Chad picks tools and routes subagents automatically. Your selection here is the main reasoning model for chat.
            </p>

            <section className="models-tab__section">
                <div className="models-tab__section-head">
                    <Cloud size={14} />
                    <h3 className="models-tab__section-title">Cloud models</h3>
                </div>
                <div className="models-tab__grid">
                    {cloudModels.length === 0 ? (
                        <div className="models-tab__empty">
                            Cloud models require your own API key. No cloud provider credentials are configured.
                        </div>
                    ) : cloudModels.map((cm) => {
                        const active = isCloudActive(cm.id, cm.name);
                        return (
                            <button
                                key={cm.id}
                                type="button"
                                className={`models-tab__card ${active ? 'is-active' : ''}`}
                                disabled={switching}
                                onClick={() => selectModel({ mode: 'cloud', id: cm.id, name: cm.name })}
                            >
                                <Cloud size={15} className="models-tab__card-icon" />
                                <span className="models-tab__card-name">{cm.name}</span>
                                {active && (
                                    <span className="models-tab__card-badge">
                                        <Check size={11} /> Active
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </div>
            </section>

            <section className="models-tab__section">
                <div className="models-tab__section-head">
                    <Cpu size={14} />
                    <h3 className="models-tab__section-title">Local models</h3>
                    <button
                        type="button"
                        className="models-tab__refresh"
                        onClick={refreshLocal}
                        disabled={isLocalScanning}
                        title="Rescan LM Studio / Ollama"
                    >
                        <RefreshCw size={13} className={isLocalScanning ? 'models-tab__spin' : ''} />
                        {isLocalScanning ? 'Scanning…' : 'Rescan'}
                    </button>
                </div>

                {isLocalScanning && localModels.length === 0 ? (
                    <div className="models-tab__scanning">
                        <Loader2 size={18} className="models-tab__spin" />
                        <span>Scanning local providers…</span>
                    </div>
                ) : !isLocalScanning && localModels.length === 0 ? (
                    <div className="models-tab__empty-state">
                        <p>No local models found.</p>
                        <p className="models-tab__empty-hint">
                            Start LM Studio or Ollama and load a model. Default ports: LM Studio (1234), Ollama (11434).
                        </p>
                    </div>
                ) : (
                    <div className="models-tab__grid">
                        {localModels.map((m) => {
                            const active = isLocalActive(m.name);
                            return (
                                <button
                                    key={m.name}
                                    type="button"
                                    className={`models-tab__card local ${active ? 'is-active' : ''}`}
                                    disabled={switching}
                                    onClick={() => selectModel(m)}
                                >
                                    <Cpu size={15} className="models-tab__card-icon" />
                                    <div className="models-tab__card-body">
                                        <span className="models-tab__card-name">
                                            {m.display_name || shortModelName(m.name)}
                                        </span>
                                        <span className="models-tab__card-meta">
                                            {m.provider}
                                            {m.size_hint ? ` · ${m.size_hint}` : ''}
                                        </span>
                                    </div>
                                    {!m.supports_tools && (
                                        <span title="May not support tool calling">
                                            <TriangleAlert size={13} className="models-tab__warn" />
                                        </span>
                                    )}
                                    {active && (
                                        <span className="models-tab__card-badge">
                                            <Check size={11} /> Active
                                        </span>
                                    )}
                                </button>
                            );
                        })}
                    </div>
                )}
            </section>

            <section className="models-tab__section">
                <h3 className="models-tab__heading">Routine builder</h3>
                <p className="models-tab__section-desc">
                    Uses the selected supervisor model to establish routines. Change the supervisor
                    above to change the routine builder too.
                </p>
            </section>
        </div>
    );
}
