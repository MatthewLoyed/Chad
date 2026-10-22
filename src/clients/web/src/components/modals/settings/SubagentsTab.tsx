import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Loader2, Bot, ShieldAlert, AlertTriangle, Cpu, Cloud, Pin } from 'lucide-react';
import Dropdown from '../../common/Dropdown';
import { useApiReady } from '../../../store';
import { readCache, writeCache } from '../../../utils/localCache';
import './SubagentsTab.css';

interface SubagentModel {
    model_name: string;
    provider: string;
    thinking_level: string;
}

interface Subagent {
    name: string;
    label: string;
    description: string;
    enabled: boolean;
    model: SubagentModel | null;
    is_model_override: boolean;
    vision_assist_enabled?: boolean;
    vision_assist_model?: string;
}

interface LocalModelOption {
    name: string;
    display_name: string;
    provider: string;
}

const FETCH_TIMEOUT_MS = 8000;

const AGENT_COLORS: Record<string, string> = {
    ui_automation_agent: '#a78bfa',
    browser_agent: '#34d399',
    research_agent: '#fbbf24',
    code_agent: '#f472b6',
    system_agent: '#94a3b8',
    traversal_agent: '#2dd4bf',
};

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
            (value) => { clearTimeout(timer); resolve(value); },
            (err) => { clearTimeout(timer); reject(err); },
        );
    });
}

function shortModelName(name: string): string {
    return name.split('/').pop()?.replace(/-/g, ' ') ?? name;
}

export default function SubagentsTab() {
    const apiReady = useApiReady();
    const [subagents, setSubagents] = useState<Subagent[]>(() => {
        return readCache<Subagent[]>('subagents_list') ?? [];
    });
    const [loading, setLoading] = useState(() => !readCache<Subagent[]>('subagents_list'));
    const [error, setError] = useState<string | null>(null);
    const [localModels, setLocalModels] = useState<LocalModelOption[]>(() => {
        return readCache<LocalModelOption[]>('subagents_local_models') ?? [];
    });
    const [savingAgent, setSavingAgent] = useState<string | null>(null);
    const hasFetchedModels = useRef(false);

    const api = apiReady ? window.pywebview?.api : undefined;

    const fetchSubagents = useCallback(() => {
        if (!api?.get_subagents) return;
        withTimeout(api.get_subagents(), FETCH_TIMEOUT_MS, 'get_subagents')
            .then((result: unknown) => {
                setError(null);
                const resultAgents = result as Subagent[];
                const list = Array.isArray(resultAgents) ? resultAgents : [];
                setSubagents(list);
                writeCache('subagents_list', list);
            })
            .catch((e) => {
                console.error('[SubagentsTab] get_subagents failed:', e);
                setError('Could not load subagents. Check that the Brain server is running.');
            })
            .finally(() => setLoading(false));
    }, [api]);

    useEffect(() => {
        fetchSubagents();
    }, [fetchSubagents]);

    useEffect(() => {
        if (!apiReady || hasFetchedModels.current) return;
        const bridge = window.pywebview?.api;
        if (!bridge?.discover_local_models) return;
        hasFetchedModels.current = true;
        withTimeout(bridge.discover_local_models(), FETCH_TIMEOUT_MS, 'discover_local_models')
            .then((result: unknown) => {
                const models = result as LocalModelOption[];
                const list = Array.isArray(models) ? models : [];
                setLocalModels(list);
                writeCache('subagents_local_models', list);
            })
            .catch((e) => console.warn('[SubagentsTab] discover_local_models failed:', e));
    }, [apiReady]);

    const handleToggle = (name: string, enabled: boolean) => {
        const bridge = window.pywebview?.api;
        if (!bridge?.toggle_subagent) return;
        bridge.toggle_subagent(name, !enabled)
            .then(() => fetchSubagents())
            .catch(console.error);
    };

    const handleModelChange = (name: string, value: string) => {
        const bridge = window.pywebview?.api;
        if (!bridge?.set_subagent_model) return;

        setSavingAgent(name);
        const modelInfo = value === '__default__'
            ? null
            : localModels.find((m) => m.name === value)
                ? {
                    model_name: value,
                    provider: localModels.find((m) => m.name === value)!.provider,
                    thinking_level: 'medium',
                }
                : null;

        bridge.set_subagent_model(name, modelInfo)
            .then(() => fetchSubagents())
            .catch(console.error)
            .finally(() => setSavingAgent(null));
    };

    const handleVisionAssistChange = (enabled: boolean, modelName?: string) => {
        const bridge = window.pywebview?.api;
        if (!bridge?.set_browser_vision_assist) return;

        bridge.set_browser_vision_assist(enabled, modelName || null)
            .then(() => fetchSubagents())
            .catch(console.error);
    };

    const modelOptions = useMemo(() => {
        const opts = [{ value: '__default__', label: 'Use built-in default' }];
        for (const m of localModels) {
            opts.push({ value: m.name, label: m.display_name || shortModelName(m.name) });
        }
        return opts;
    }, [localModels]);

    const isBrowserAgentDisabled = subagents.some((s) => s.name === 'browser_agent' && !s.enabled);

    const enabledCount = useMemo(() => subagents.filter((s) => s.enabled).length, [subagents]);

    return (
        <div className="subagents-tab">
            <div className="subagents-tab__stats">
                <span>{enabledCount} of {subagents.length || '—'} agents enabled</span>
            </div>

            <p className="subagents-tab__intro">
                Specialist agents Chad can delegate to. Each can run on its own pinned local model or inherit its built-in default.
            </p>

            {isBrowserAgentDisabled && (
                <div className="subagents-warning">
                    <ShieldAlert size={18} className="subagents-warning__icon" />
                    <div className="subagents-warning__text">
                        <strong>Browser Agent is disabled</strong>
                        <span>Chad won&apos;t automate web pages — only direct API/MCP or local commands.</span>
                    </div>
                </div>
            )}

            {loading && (
                <div className="subagents-loading">
                    <Loader2 size={18} className="subagents-spin" />
                    Loading subagents…
                </div>
            )}

            {!loading && error && (
                <div className="subagents-error">
                    <AlertTriangle size={18} />
                    <span>{error}</span>
                </div>
            )}

            {!loading && !error && subagents.length === 0 && (
                <div className="subagents-empty">No subagents configured.</div>
            )}

            {!loading && !error && subagents.length > 0 && (
                <div className="subagents-grid">
                    {subagents.map((agent) => {
                        const accent = AGENT_COLORS[agent.name] ?? '#818cf8';
                        const selectValue = agent.is_model_override
                            ? agent.model?.model_name ?? '__default__'
                            : '__default__';
                        const effectiveModel = agent.model;
                        const isPinned = agent.is_model_override;
                        const isBuiltIn = !isPinned && !!effectiveModel?.model_name;

                        return (
                            <article
                                key={agent.name}
                                className={`subagent-card ${!agent.enabled ? 'is-disabled' : ''}`}
                                style={{ '--agent-accent': accent } as React.CSSProperties}
                            >
                                <header className="subagent-card__header">
                                    <div className="subagent-card__identity">
                                        <div className="subagent-card__avatar">
                                            <Bot size={18} />
                                        </div>
                                        <div className="subagent-card__titles">
                                            <h3 className="subagent-card__name">{agent.label}</h3>
                                            <span className="subagent-card__id">@{agent.name}</span>
                                        </div>
                                    </div>

                                    <label className="subagent-switch" title={agent.enabled ? 'Disable agent' : 'Enable agent'}>
                                        <input
                                            type="checkbox"
                                            checked={agent.enabled}
                                            onChange={() => handleToggle(agent.name, agent.enabled)}
                                        />
                                        <span className="subagent-switch__track" aria-hidden="true">
                                            <span className="subagent-switch__thumb" />
                                        </span>
                                        <span className="subagent-switch__label">
                                            {agent.enabled ? 'On' : 'Off'}
                                        </span>
                                    </label>
                                </header>

                                <p className="subagent-card__desc">{agent.description}</p>

                                <div className="subagent-card__model">
                                    <div className="subagent-card__model-current">
                                        <div className="subagent-card__model-icon-wrap">
                                            {effectiveModel?.provider === 'cloud' || !effectiveModel
                                                ? <Cloud size={14} />
                                                : <Cpu size={14} />}
                                        </div>
                                        <div className="subagent-card__model-info">
                                            <span className="subagent-card__model-label">Model</span>
                                            <span className="subagent-card__model-name">
                                                {effectiveModel?.model_name ?? 'Supervisor default'}
                                            </span>
                                            {effectiveModel && (
                                                <span className="subagent-card__model-meta">
                                                    {effectiveModel.provider}
                                                    {effectiveModel.thinking_level
                                                        ? ` · ${effectiveModel.thinking_level} thinking`
                                                        : ''}
                                                </span>
                                            )}
                                        </div>
                                        <div className="subagent-card__model-badges">
                                            {isPinned && (
                                                <span className="subagent-badge subagent-badge--pinned">
                                                    <Pin size={10} /> Pinned
                                                </span>
                                            )}
                                            {isBuiltIn && (
                                                <span className="subagent-badge subagent-badge--builtin">Built-in</span>
                                            )}
                                            {!effectiveModel && (
                                                <span className="subagent-badge subagent-badge--inherit">Inherited</span>
                                            )}
                                        </div>
                                    </div>

                                    <div className="subagent-card__model-picker">
                                        <Dropdown
                                            value={selectValue}
                                            options={modelOptions}
                                            onChange={(v) => handleModelChange(agent.name, v)}
                                            disabled={savingAgent === agent.name || !agent.enabled}
                                            placeholder="Select model"
                                        />
                                        {savingAgent === agent.name && (
                                            <Loader2 size={14} className="subagents-spin subagent-card__saving" />
                                        )}
                                    </div>
                                </div>

                                {agent.name === 'browser_agent' && (
                                    <div className="subagent-card__vision-assist">
                                        <div className="subagent-vision-toggle">
                                            <div className="subagent-vision-toggle__info">
                                                <span className="subagent-vision-toggle__label">Vision-Assist Fallback</span>
                                                <span className="subagent-vision-toggle__desc">Ground clicks using screenshot analysis on retry</span>
                                            </div>
                                            <label className="subagent-switch" title="Toggle vision-assist">
                                                <input
                                                    type="checkbox"
                                                    checked={!!agent.vision_assist_enabled}
                                                    disabled={!agent.enabled}
                                                    onChange={(e) => handleVisionAssistChange(e.target.checked, agent.vision_assist_model)}
                                                />
                                                <span className="subagent-switch__track" aria-hidden="true">
                                                    <span className="subagent-switch__thumb" />
                                                </span>
                                            </label>
                                        </div>

                                        {agent.vision_assist_enabled && (
                                            <div className="subagent-vision-model-picker">
                                                <span className="subagent-card__model-label">Vision Model</span>
                                                <Dropdown
                                                    value={agent.vision_assist_model || 'qwen/qwen3-vl-8b'}
                                                    options={modelOptions.filter(opt => opt.value !== '__default__')}
                                                    onChange={(v) => handleVisionAssistChange(true, v)}
                                                    disabled={!agent.enabled}
                                                    placeholder="Select vision model"
                                                />
                                            </div>
                                        )}
                                    </div>
                                )}
                            </article>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
