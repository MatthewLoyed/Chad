/**
 * ModelSelector — Local AI Model Picker
 *
 * A compact dropdown that lets the user switch between:
 *   • ☁️ Cloud models with configured credentials
 *   • 🖥️ Local models from LM Studio / Ollama
 *
 * This version uses global state from useAppStore for available models,
 * populated during application boot in App.tsx.
 */

import { useRef, useState, useCallback, useMemo, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Cpu, Cloud, ChevronDown, RefreshCw, TriangleAlert, Loader2, Settings2 } from 'lucide-react';
import { useAppStore } from '../../store';
import type { ActiveModelInfo, LocalModelInfo } from '../../store';
import ModelSettingsModal from '../modals/settings/ModelSettingsModal';
import './ModelSelector.css';

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function getApi() {
    return window.pywebview?.api ?? null;
}

/** Max wait for bridge discovery before showing the empty state. */
const LOCAL_DISCOVERY_TIMEOUT_MS = 8000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
            (value) => {
                clearTimeout(timer);
                resolve(value);
            },
            (err) => {
                clearTimeout(timer);
                reject(err);
            }
        );
    });
}

// ──────────────────────────────────────────────────────────────────────────────
// Component
// ──────────────────────────────────────────────────────────────────────────────

export default function ModelSelector() {
    const [isOpen, setIsOpen] = useState(false);
    const [isSwitching, setIsSwitching] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const activeModel = useAppStore(state => state.activeModel);
    const setActiveModel = useAppStore(state => state.setActiveModel);
    const cloudModels = useAppStore(state => state.availableCloudModels);
    const localModels = useAppStore(state => state.availableLocalModels) as LocalModelInfo[];
    const setAvailableLocalModels = useAppStore(state => state.setAvailableLocalModels);
    const isLocalScanning = useAppStore(state => state.isLocalScanning);
    const setIsLocalScanning = useAppStore(state => state.setIsLocalScanning);
    
    const dropdownRef = useRef<HTMLDivElement>(null);
    const attemptedDiscovery = useRef(false);
    const isRefreshing = isLocalScanning; // Sync for legacy UI references

    // ── Manual Refresh ────────────────────────────────────────────────────────
    const refreshModels = useCallback(async () => {
        const api = getApi();
        if (!api || typeof api.discover_local_models !== 'function') {
            return;
        }

        attemptedDiscovery.current = true;
        setIsLocalScanning(true);
        try {
            const models = await withTimeout(
                api.discover_local_models(),
                LOCAL_DISCOVERY_TIMEOUT_MS,
                'discover_local_models'
            );
            setAvailableLocalModels(Array.isArray(models) ? models : []);
        } catch (e) {
            console.warn('[ModelSelector] discover_local_models failed or timed out:', e);
            // Preserve usable cached models when a refresh fails.
        } finally {
            setIsLocalScanning(false);
        }
    }, [setAvailableLocalModels, setIsLocalScanning]);

    // ── Select a model ────────────────────────────────────────────────────────
    const selectModel = useCallback(async (modelInfo: LocalModelInfo | { mode: 'cloud', id: string, name: string }) => {
        const api = getApi();
        if (!api || typeof api.set_active_model !== 'function') return;

        setIsSwitching(true);
        setIsOpen(false);
        try {
            const payload = ('mode' in modelInfo && modelInfo.mode === 'cloud')
                ? { mode: 'cloud', name: modelInfo.id, display_name: modelInfo.name }
                : modelInfo;

            const success = await api.set_active_model(payload);
            if (success) {
                if ('mode' in modelInfo && modelInfo.mode === 'cloud') {
                    setActiveModel({
                        mode: 'cloud',
                        name: modelInfo.name,
                        provider: 'cloud',
                        supports_tools: true,
                        thinking_enabled: null,
                        model_settings: {},
                    });
                } else {
                    // Fetch full model info (includes model_settings) from backend
                    try {
                        const info: ActiveModelInfo | null = typeof api.get_active_model_info === 'function'
                            ? await api.get_active_model_info() as ActiveModelInfo
                            : null;
                        if (info) {
                            setActiveModel(info);
                        } else {
                            const m = modelInfo as LocalModelInfo;
                            setActiveModel({
                                mode: 'local',
                                name: m.name,
                                provider: m.provider,
                                supports_tools: m.supports_tools,
                                tools_verified: m.tools_verified,
                                thinking_enabled: null,
                                model_settings: {},
                            });
                        }
                    } catch {
                        const m = modelInfo as LocalModelInfo;
                        setActiveModel({
                            mode: 'local',
                            name: m.name,
                            provider: m.provider,
                            supports_tools: m.supports_tools,
                            tools_verified: m.tools_verified,
                            thinking_enabled: null,
                            model_settings: {},
                        });
                    }
                }
            }
        } catch (e) {
            console.error('[ModelSelector] set_active_model failed:', e);
        } finally {
            setIsSwitching(false);
        }
    }, [setActiveModel]);

    // ── Render label ──────────────────────────────────────────────────────────
    const labelContent = useMemo(() => {
        if (isSwitching) return <><Loader2 size={13} className="spin" /> Switching...</>;
        if (!activeModel) return <><Cloud size={13} /> Loading...</>;
        
        const label = activeModel.name;
        // Clean up common model IDs if present, falling back safely
        const short = label
            .replace(/Gemini\s+/gi, 'G ')
            .replace(/Claude\s+/gi, 'C ')
            .replace(/GPT[-\s]*/gi, 'GPT ')
            .split('/').pop()?.split('-').slice(0, 3).join(' ') ?? label;

        if (activeModel.mode === 'cloud' || activeModel.hosting === 'cloud') return <><Cloud size={13} /> {short}</>;
        return <><Cpu size={13} /> {short}</>;
    }, [isSwitching, activeModel]);

    // Handle clicking outside to close
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    // ── JIT Lazy Scanning ─────────────────────────────────────────────────────
    // Trigger local model discovery only when user actively engages the dropdown
    useEffect(() => {
        if (isOpen && localModels.length === 0 && !isRefreshing && !attemptedDiscovery.current) {
            console.log('[ModelSelector] Dropdown opened with empty cache, triggering JIT scan...');
            refreshModels();
        }
    }, [isOpen, localModels.length, isRefreshing, refreshModels]);

    const showSettingsGear = !isSwitching && activeModel?.mode === 'local' && activeModel.hosting !== 'cloud';
    const showToolWarning = activeModel?.mode === 'local'
        && activeModel.tools_verified
        && !activeModel.supports_tools;

    return (
        <>
        <div className="model-selector" ref={dropdownRef}>
            <motion.button
                type="button"
                className={`model-selector__trigger ${activeModel?.mode === 'local' ? 'local' : ''}`}
                onClick={() => setIsOpen(v => !v)}
                title={`Active: ${activeModel?.name}`}
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
            >
                <div className="model-selector__trigger-content">
                    {labelContent}
                    {showToolWarning && (
                        <span title="Tool calling was tested and is not supported on this model">
                            <TriangleAlert
                                size={11}
                                className="model-selector__warn"
                            />
                        </span>
                    )}
                </div>
                <ChevronDown size={12} className={`model-selector__chevron ${isOpen ? 'open' : ''}`} />
            </motion.button>

            {/* Gear button — only for local models */}
            {showSettingsGear && (
                <motion.button
                    type="button"
                    className="model-selector__settings-btn"
                    onClick={e => { e.stopPropagation(); setIsSettingsOpen(v => !v); }}
                    title="Model generation settings"
                    whileHover={{ scale: 1.1 }}
                    whileTap={{ scale: 0.9 }}
                >
                    <Settings2 size={12} />
                </motion.button>
            )}

            <AnimatePresence>
                {isOpen && (
                    <motion.div
                        className="model-selector__dropdown"
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.95 }}
                        transition={{ duration: 0.15 }}
                    >
                        <div className="model-selector__section-label">Cloud</div>
                        <div className="model-selector__list">
                            {cloudModels.length === 0 ? (
                                <div className="model-selector__scanning">
                                    <span>Cloud models require your own API key. No cloud provider credentials are configured. Provider charges may apply.</span>
                                </div>
                            ) : cloudModels.map((cm) => (
                                <button
                                    key={cm.id}
                                    type="button"
                                    className={`model-selector__item ${activeModel?.mode === 'cloud' && (activeModel?.name === cm.name || activeModel?.name === cm.id) ? 'active' : ''}`}
                                    onClick={() => selectModel({ mode: 'cloud', id: cm.id, name: cm.name })}
                                >
                                    <Cloud size={13} />
                                    <span className="model-selector__item-name">{cm.name}</span>
                                    {activeModel?.mode === 'cloud' && (activeModel?.name === cm.name || activeModel?.name === cm.id) && (
                                        <span className="model-selector__badge active-badge">Active</span>
                                    )}
                                </button>
                            ))}
                        </div>

                        <div className="model-selector__divider" />

                        <div className="model-selector__section-header">
                            <span className="model-selector__section-label">Local</span>
                            <div className="model-selector__section-actions">
                                <button
                                    className="model-selector__refresh"
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); refreshModels(); }}
                                    disabled={isRefreshing}
                                >
                                    <RefreshCw size={11} />
                                </button>
                            </div>
                        </div>

                        <div className="model-selector__list model-selector__list--local" data-scrollable="true">
                            {isRefreshing ? (
                                <div className="model-selector__scanning">
                                    <Loader2 size={16} className="spin" />
                                    <span>Scanning local providers...</span>
                                </div>
                            ) : localModels.length === 0 ? (
                                <div className="model-selector__empty">
                                    Could not find any local models.
                                    <div className="model-selector__hint">
                                        Make sure LM Studio or Ollama is running and has a model loaded.
                                    </div>
                                    <div className="model-selector__hint-sub">
                                        Default ports: LM Studio (1234), Ollama (11434)
                                    </div>
                                </div>
                            ) : null}

                            {localModels.map((m) => (
                                <button
                                    key={m.name}
                                    type="button"
                                    className={`model-selector__item ${activeModel?.mode === 'local' && activeModel?.name === m.name ? 'active' : ''}`}
                                    onClick={() => selectModel(m)}
                                >
                                    <Cpu size={13} />
                                    <span className="model-selector__item-name">
                                        {m.display_name}
                                        {m.size_hint && <span className="model-selector__size">{m.size_hint}</span>}
                                    </span>
                                    {activeModel?.mode === 'local' && activeModel?.name === m.name && (
                                        <span className="model-selector__badge active-badge">Active</span>
                                    )}
                                </button>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>

        <ModelSettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
        </>
    );
}
