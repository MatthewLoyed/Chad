import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Check, X, Wrench, Sparkles, Lightbulb, Hammer, Loader2 } from 'lucide-react';
import Modal from '../base/Modal';
import { useAppStore } from '../../../store';
import type { CapabilitySuggestion } from '../../../store';
import './SuggestionsModal.css';

interface SuggestionsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

type BuildState =
    | { phase: 'proposing' }
    | { phase: 'review'; approvalToken: string; skillName: string; targetDir: string; preview: string }
    | { phase: 'building' }
    | { phase: 'built'; skillName: string; written: string[] }
    | { phase: 'error'; message: string };

interface ProposeSkillBuildResult {
    success: boolean;
    error?: string;
    approval_token?: string;
    skill_name?: string;
    target_dir?: string;
    preview?: string;
}

interface CommitSkillBuildResult {
    success: boolean;
    error?: string;
    skill_name?: string;
    written?: string[];
}

export default function SuggestionsModal({ isOpen, onClose }: SuggestionsModalProps) {
    const suggestions = useAppStore(state => state.capabilitySuggestions);
    const setCapabilitySuggestions = useAppStore(state => state.setCapabilitySuggestions);
    const removeCapabilitySuggestion = useAppStore(state => state.removeCapabilitySuggestion);
    const [builds, setBuilds] = useState<Record<string, BuildState>>({});

    // Fetch the pending queue whenever the panel opens.
    useEffect(() => {
        if (isOpen && window.pywebview?.api) {
            const api = window.pywebview.api;
            if (typeof api.get_capability_suggestions === 'function') {
                api.get_capability_suggestions(10).then((result) => {
                    if (Array.isArray(result)) {
                        setCapabilitySuggestions(result as CapabilitySuggestion[]);
                    }
                }).catch(console.error);
            }
        }
    }, [isOpen, setCapabilitySuggestions]);

    const updateStatus = (id: string, status: 'accepted' | 'dismissed') => {
        // Optimistically remove the card; the backend marks the status.
        removeCapabilitySuggestion(id);
        const api = window.pywebview?.api;
        if (api && typeof api.set_capability_suggestion_status === 'function') {
            api.set_capability_suggestion_status(id, status).catch(console.error);
        }
    };

    const proposeBuild = (s: CapabilitySuggestion) => {
        const api = window.pywebview?.api;
        if (!api || typeof api.propose_capability_skill_build !== 'function') return;
        setBuilds(prev => ({ ...prev, [s.id]: { phase: 'proposing' } }));
        api.propose_capability_skill_build(s.id).then((raw) => {
            const result = raw as ProposeSkillBuildResult;
            if (result?.success) {
                setBuilds(prev => ({ ...prev, [s.id]: {
                    phase: 'review',
                    approvalToken: result.approval_token || '',
                    skillName: result.skill_name || '',
                    targetDir: result.target_dir || '',
                    preview: result.preview || '',
                } }));
            } else {
                setBuilds(prev => ({ ...prev, [s.id]: { phase: 'error', message: result?.error || 'Failed to draft skill' } }));
            }
        }).catch((err) => {
            setBuilds(prev => ({ ...prev, [s.id]: { phase: 'error', message: String(err) } }));
        });
    };

    const confirmBuild = (s: CapabilitySuggestion, approvalToken: string) => {
        const api = window.pywebview?.api;
        if (!api || typeof api.commit_capability_skill_build !== 'function') return;
        setBuilds(prev => ({ ...prev, [s.id]: { phase: 'building' } }));
        api.commit_capability_skill_build(s.id, approvalToken).then((raw) => {
            const result = raw as CommitSkillBuildResult;
            if (result?.success) {
                setBuilds(prev => ({ ...prev, [s.id]: {
                    phase: 'built', skillName: result.skill_name || '', written: result.written || [],
                } }));
            } else {
                setBuilds(prev => ({ ...prev, [s.id]: { phase: 'error', message: result?.error || 'Build failed' } }));
            }
        }).catch((err) => {
            setBuilds(prev => ({ ...prev, [s.id]: { phase: 'error', message: String(err) } }));
        });
    };

    const cancelBuild = (id: string) => {
        setBuilds(prev => {
            const next = { ...prev };
            delete next[id];
            return next;
        });
    };

    const finishBuild = (id: string) => {
        cancelBuild(id);
        removeCapabilitySuggestion(id);
    };

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="Chad's Recommendations">
            {suggestions.length === 0 ? (
                <div className="suggestions-empty">
                    <Lightbulb size={32} strokeWidth={1.5} />
                    <p className="suggestions-empty__title">No suggestions right now</p>
                    <p className="suggestions-empty__sub">
                        Chad will propose new tools or skills here as he spots recurring gaps.
                    </p>
                </div>
            ) : (
                <div className="suggestions-list">
                    {suggestions.map((s) => {
                        const build = builds[s.id];
                        return (
                        <div key={s.id} className="suggestion-card">
                            <div className="suggestion-card__head">
                                <span className={`suggestion-card__badge suggestion-card__badge--${s.kind}`}>
                                    {s.kind === 'tool'
                                        ? <Wrench size={12} strokeWidth={2} />
                                        : <Sparkles size={12} strokeWidth={2} />}
                                    {s.kind}
                                </span>
                                <span className="suggestion-card__title">{s.title}</span>
                            </div>

                            {s.description && (
                                <p className="suggestion-card__desc">{s.description}</p>
                            )}

                            {s.rationale && (
                                <p className="suggestion-card__rationale">
                                    <span className="suggestion-card__rationale-label">Why</span>
                                    {s.rationale}
                                </p>
                            )}

                            <div className="suggestion-card__meta">
                                <span className="suggestion-card__metric" title="Model confidence">
                                    {Math.round((s.confidence ?? 0) * 100)}% confidence
                                </span>
                                <span className="suggestion-card__metric" title="Number of signals backing this suggestion">
                                    {s.cluster_size} signal{s.cluster_size === 1 ? '' : 's'}
                                </span>
                            </div>

                            {!build && (
                                <div className="suggestion-card__actions">
                                    <motion.button
                                        className="suggestion-btn suggestion-btn--accept"
                                        onClick={() => s.kind === 'skill' ? proposeBuild(s) : updateStatus(s.id, 'accepted')}
                                        whileHover={{ scale: 1.03 }}
                                        whileTap={{ scale: 0.97 }}
                                    >
                                        <Check size={14} strokeWidth={2.5} /> Accept
                                    </motion.button>
                                    <motion.button
                                        className="suggestion-btn suggestion-btn--dismiss"
                                        onClick={() => updateStatus(s.id, 'dismissed')}
                                        whileHover={{ scale: 1.03 }}
                                        whileTap={{ scale: 0.97 }}
                                    >
                                        <X size={14} strokeWidth={2.5} /> Dismiss
                                    </motion.button>
                                </div>
                            )}

                            {build?.phase === 'proposing' && (
                                <div className="suggestion-build suggestion-build--pending">
                                    <Loader2 size={14} className="suggestion-build__spinner" /> Drafting skill files…
                                </div>
                            )}

                            {build?.phase === 'review' && (
                                <div className="suggestion-build">
                                    <p className="suggestion-build__preview">{build.preview}</p>
                                    <div className="suggestion-card__actions">
                                        <motion.button
                                            className="suggestion-btn suggestion-btn--accept"
                                            onClick={() => confirmBuild(s, build.approvalToken)}
                                            whileHover={{ scale: 1.03 }}
                                            whileTap={{ scale: 0.97 }}
                                        >
                                            <Hammer size={14} strokeWidth={2.5} /> Build it
                                        </motion.button>
                                        <motion.button
                                            className="suggestion-btn suggestion-btn--dismiss"
                                            onClick={() => cancelBuild(s.id)}
                                            whileHover={{ scale: 1.03 }}
                                            whileTap={{ scale: 0.97 }}
                                        >
                                            Cancel
                                        </motion.button>
                                    </div>
                                </div>
                            )}

                            {build?.phase === 'building' && (
                                <div className="suggestion-build suggestion-build--pending">
                                    <Loader2 size={14} className="suggestion-build__spinner" /> Writing skill to disk…
                                </div>
                            )}

                            {build?.phase === 'built' && (
                                <div className="suggestion-build suggestion-build--success">
                                    <p>Built <code>{build.skillName}</code> — {build.written.length} file{build.written.length === 1 ? '' : 's'} written.</p>
                                    <motion.button
                                        className="suggestion-btn suggestion-btn--dismiss"
                                        onClick={() => finishBuild(s.id)}
                                        whileHover={{ scale: 1.03 }}
                                        whileTap={{ scale: 0.97 }}
                                    >
                                        Done
                                    </motion.button>
                                </div>
                            )}

                            {build?.phase === 'error' && (
                                <div className="suggestion-build suggestion-build--error">
                                    <p>{build.message}</p>
                                    <motion.button
                                        className="suggestion-btn suggestion-btn--dismiss"
                                        onClick={() => cancelBuild(s.id)}
                                        whileHover={{ scale: 1.03 }}
                                        whileTap={{ scale: 0.97 }}
                                    >
                                        Dismiss
                                    </motion.button>
                                </div>
                            )}
                        </div>
                        );
                    })}
                </div>
            )}
        </Modal>
    );
}
