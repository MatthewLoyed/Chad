/**
 * ModelSettingsModal
 *
 * Lets the user view and edit all per-model generation settings that are
 * stored in config.yaml under local_models.model_settings.<model_id>.
 *
 * Settings exposed:
 *   - thinking (on/off)
 *   - reasoning_effort (none/low/medium/high)  — only when thinking=on
 *   - thinking_budget (token cap)               — only when thinking=on
 *   - top_p, top_k, repeat_penalty
 *
 * On Save → calls api.update_model_settings() → backend writes config.yaml
 *           → store is updated optimistically so ChatInput toggle stays in sync
 */

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, BrainCircuit, Sliders, RotateCcw, Save } from 'lucide-react';
import { useAppStore } from '../../../store';
import type { ModelSettings } from '../../../store';
import './ModelSettingsModal.css';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getApi() {
    return window.pywebview?.api ?? null;
}

const REASONING_EFFORT_OPTIONS = ['none', 'low', 'medium', 'high'] as const;
type ReasoningEffort = typeof REASONING_EFFORT_OPTIONS[number];

/** Non-nullable working draft — all fields are definite values while editing */
type DraftSettings = {
    thinking: boolean;
    reasoning_effort: ReasoningEffort;
    thinking_budget: number;
    top_p: number;
    top_k: number;
    repeat_penalty: number;
};

const DEFAULTS: DraftSettings = {
    thinking: false,
    reasoning_effort: 'medium',
    thinking_budget: 32768,
    top_p: 0.8,
    top_k: 20,
    repeat_penalty: 1.05,
};

// ─── Types ────────────────────────────────────────────────────────────────────

interface Props {
    isOpen: boolean;
    onClose: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ModelSettingsModal({ isOpen, onClose }: Props) {
    const activeModel = useAppStore(s => s.activeModel);
    const updateActiveModelSettings = useAppStore(s => s.updateActiveModelSettings);

    // Local draft state — only committed on Save
    const [draft, setDraft] = useState<DraftSettings>(DEFAULTS);
    const [saving, setSaving] = useState(false);
    const [savedOk, setSavedOk] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Sync draft from store whenever the modal opens or active model changes
    useEffect(() => {
        if (!isOpen) return;
        const src = activeModel?.model_settings ?? {};
        setDraft({
            thinking:          src.thinking          ?? DEFAULTS.thinking,
            reasoning_effort:  (src.reasoning_effort as ReasoningEffort) ?? DEFAULTS.reasoning_effort,
            thinking_budget:   src.thinking_budget   ?? DEFAULTS.thinking_budget,
            top_p:             src.top_p             ?? DEFAULTS.top_p,
            top_k:             src.top_k             ?? DEFAULTS.top_k,
            repeat_penalty:    src.repeat_penalty    ?? DEFAULTS.repeat_penalty,
        });
        setSavedOk(false);
        setError(null);
    }, [isOpen, activeModel?.name, activeModel?.model_settings]);

    const update = useCallback(<K extends keyof DraftSettings>(
        key: K, value: DraftSettings[K]
    ) => {
        setDraft(d => ({ ...d, [key]: value }));
        setSavedOk(false);
    }, []);

    const handleReset = useCallback(() => {
        setDraft(DEFAULTS);
        setSavedOk(false);
        setError(null);
    }, []);

    const handleSave = useCallback(async () => {
        const api = getApi();
        if (!api || !activeModel?.name) return;

        setSaving(true);
        setError(null);
        try {
            const payload: ModelSettings = {
                thinking:         draft.thinking,
                reasoning_effort: draft.reasoning_effort,
                thinking_budget:  draft.thinking_budget,
                top_p:            draft.top_p,
                top_k:            draft.top_k,
                repeat_penalty:   draft.repeat_penalty,
            };

            const ok = await api.update_model_settings(activeModel.name, JSON.stringify(payload));
            if (ok) {
                updateActiveModelSettings(payload);
                setSavedOk(true);
                setTimeout(() => setSavedOk(false), 2000);
            } else {
                setError('Save failed — check server logs');
            }
        } catch (e) {
            setError(String(e));
        } finally {
            setSaving(false);
        }
    }, [activeModel?.name, draft, updateActiveModelSettings]);

    if (!activeModel || activeModel.mode !== 'local') return null;

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    className="msm-overlay"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    onClick={onClose}
                >
                    <motion.div
                        className="msm-panel"
                        initial={{ opacity: 0, x: 40, scale: 0.97 }}
                        animate={{ opacity: 1, x: 0, scale: 1 }}
                        exit={{ opacity: 0, x: 40, scale: 0.97 }}
                        transition={{ type: 'spring', stiffness: 320, damping: 28 }}
                        onClick={e => e.stopPropagation()}
                    >
                        {/* Header */}
                        <div className="msm-header">
                            <div className="msm-header-left">
                                <Sliders size={15} />
                                <span className="msm-title">Model Settings</span>
                            </div>
                            <div className="msm-header-right">
                                <span className="msm-model-badge">{activeModel.name}</span>
                                <button className="msm-close" onClick={onClose} title="Close">
                                    <X size={15} />
                                </button>
                            </div>
                        </div>

                        <div className="msm-body">
                            {/* ── Thinking ──────────────────────────────────── */}
                            <section className="msm-section">
                                <div className="msm-section-title">
                                    <BrainCircuit size={13} />
                                    Reasoning
                                </div>

                                <div className="msm-row">
                                    <label className="msm-label">
                                        Extended thinking
                                        <span className="msm-hint">
                                            Auto-disabled on tool-calling turns to prevent trapped output
                                        </span>
                                    </label>
                                    <button
                                        className={`msm-toggle ${draft.thinking ? 'on' : 'off'}`}
                                        onClick={() => update('thinking', !draft.thinking)}
                                        title={draft.thinking ? 'Click to disable' : 'Click to enable'}
                                    >
                                        {draft.thinking ? 'ON' : 'OFF'}
                                    </button>
                                </div>

                                <AnimatePresence>
                                    {draft.thinking && (
                                        <motion.div
                                            className="msm-thinking-sub"
                                            initial={{ opacity: 0, height: 0 }}
                                            animate={{ opacity: 1, height: 'auto' }}
                                            exit={{ opacity: 0, height: 0 }}
                                            transition={{ duration: 0.18 }}
                                        >
                                            <div className="msm-row">
                                                <label className="msm-label">
                                                    Reasoning effort
                                                    <span className="msm-hint">LM Studio v0.4.8+ budget hint</span>
                                                </label>
                                                <div className="msm-segmented">
                                                    {REASONING_EFFORT_OPTIONS.map(opt => (
                                                        <button
                                                            key={opt}
                                                            className={`msm-seg-btn ${draft.reasoning_effort === opt ? 'active' : ''}`}
                                                            onClick={() => update('reasoning_effort', opt)}
                                                        >
                                                            {opt}
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>

                                            <div className="msm-row">
                                                <label className="msm-label">
                                                    Thinking budget
                                                    <span className="msm-hint">Hard token cap (overrides effort hint)</span>
                                                </label>
                                                <div className="msm-number-group">
                                                    <input
                                                        type="number"
                                                        className="msm-number"
                                                        min={256}
                                                        max={262144}
                                                        step={1024}
                                                        value={draft.thinking_budget}
                                                        onChange={e => update('thinking_budget', Number(e.target.value))}
                                                    />
                                                    <span className="msm-unit">tokens</span>
                                                </div>
                                            </div>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </section>

                            {/* ── Generation ────────────────────────────────── */}
                            <section className="msm-section">
                                <div className="msm-section-title">
                                    <Sliders size={13} />
                                    Generation
                                </div>

                                <div className="msm-row">
                                    <label className="msm-label">
                                        Top P
                                        <span className="msm-hint">Nucleus sampling cutoff (0–1)</span>
                                    </label>
                                    <div className="msm-slider-group">
                                        <input
                                            type="range"
                                            className="msm-slider"
                                            min={0.0} max={1.0} step={0.01}
                                            value={draft.top_p}
                                            onChange={e => update('top_p', Number(e.target.value))}
                                        />
                                        <span className="msm-slider-val">{draft.top_p.toFixed(2)}</span>
                                    </div>
                                </div>

                                <div className="msm-row">
                                    <label className="msm-label">
                                        Top K
                                        <span className="msm-hint">Vocabulary candidates per step</span>
                                    </label>
                                    <div className="msm-number-group">
                                        <input
                                            type="number"
                                            className="msm-number"
                                            min={1} max={200} step={1}
                                            value={draft.top_k}
                                            onChange={e => update('top_k', Number(e.target.value))}
                                        />
                                    </div>
                                </div>

                                <div className="msm-row">
                                    <label className="msm-label">
                                        Repeat penalty
                                        <span className="msm-hint">Penalise repeated tokens (&gt;1 = more)</span>
                                    </label>
                                    <div className="msm-slider-group">
                                        <input
                                            type="range"
                                            className="msm-slider"
                                            min={0.5} max={2.0} step={0.01}
                                            value={draft.repeat_penalty}
                                            onChange={e => update('repeat_penalty', Number(e.target.value))}
                                        />
                                        <span className="msm-slider-val">{draft.repeat_penalty.toFixed(2)}</span>
                                    </div>
                                </div>
                            </section>
                        </div>

                        {/* Footer */}
                        <div className="msm-footer">
                            {error && <span className="msm-error">{error}</span>}
                            {savedOk && <span className="msm-saved">Saved ✓</span>}
                            <div className="msm-footer-actions">
                                <button className="msm-btn msm-btn--ghost" onClick={handleReset} title="Reset to Qwen3 defaults">
                                    <RotateCcw size={13} />
                                    Reset
                                </button>
                                <button
                                    className={`msm-btn msm-btn--primary ${saving ? 'saving' : ''}`}
                                    onClick={handleSave}
                                    disabled={saving}
                                >
                                    <Save size={13} />
                                    {saving ? 'Saving…' : 'Save'}
                                </button>
                            </div>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
