import { useState, useEffect, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    ListOrdered,
    Plus,
    Play,
    Trash2,
    Pencil,
    ChevronUp,
    ChevronDown,
    X,
    Code2,
    Loader2,
    Sparkles,
    ChevronRight,
} from 'lucide-react';
import WorkspacePane from './WorkspacePane';
import { ErrorState, EmptyState } from '../common/AsyncState';
import { readCache, writeCache } from '../../utils/localCache';
import './WorkflowsWorkspace.css';

const CACHE_KEY = 'routines';

export interface RoutineStep {
    tool_name: string;
    params: Record<string, unknown>;
    label?: string;
}

export interface Routine {
    id: string;
    name: string;
    description: string | null;
    steps: RoutineStep[];
}

interface ToolCatalogEntry {
    name: string;
    description: string;
    category: string;
    parameters: {
        type?: string;
        properties?: Record<string, { type?: string; description?: string }>;
        required?: string[];
    };
}

interface DraftStep {
    id: string;
    tool_name: string;
    label: string;
    params: { key: string; value: string }[];
    expanded?: boolean;
}

interface DraftState {
    editingId: string | null;
    name: string;
    description: string;
    steps: DraftStep[];
}

const uid = () => `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

const emptyDraft = (): DraftState => ({
    editingId: null,
    name: '',
    description: '',
    steps: [{ id: uid(), tool_name: '', label: '', params: [], expanded: true }],
});

function RoutinesSkeleton() {
    return (
        <div className="wf-grid routines-skeleton-grid" aria-busy="true" aria-label="Loading routines">
            {[1, 2, 3, 4].map((idx) => (
                <div key={idx} className="wf-card routines-skeleton-card">
                    <div className="wf-card__head">
                        <div className="routines-skeleton-line routines-skeleton-line--title" style={{ width: idx % 2 === 0 ? '60%' : '75%' }} />
                    </div>
                    <div className="routines-skeleton-line routines-skeleton-line--desc" style={{ width: '90%' }} />
                    <div className="routines-skeleton-line routines-skeleton-line--desc" style={{ width: '50%' }} />
                    <div className="wf-card__steps" style={{ marginTop: '12px' }}>
                        <div className="routines-skeleton-pill" style={{ width: '60px' }} />
                        <div className="routines-skeleton-pill" style={{ width: '85px' }} />
                        <div className="routines-skeleton-pill" style={{ width: '50px' }} />
                    </div>
                </div>
            ))}
        </div>
    );
}

function paramsToRows(params: Record<string, unknown>): { key: string; value: string }[] {
    return Object.entries(params || {}).map(([key, value]) => ({
        key,
        value: typeof value === 'string' ? value : JSON.stringify(value),
    }));
}

function rowsToParams(rows: { key: string; value: string }[]): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const { key, value } of rows) {
        if (!key.trim()) continue;
        try {
            out[key] = JSON.parse(value);
        } catch {
            out[key] = value;
        }
    }
    return out;
}

function routineToDraft(r: Routine): DraftState {
    return {
        editingId: r.id,
        name: r.name,
        description: r.description || '',
        steps: (r.steps || []).map((s) => ({
            id: uid(),
            tool_name: s.tool_name,
            label: s.label || '',
            params: paramsToRows(s.params),
            expanded: false,
        })),
    };
}

function schemaRowsForTool(tool: ToolCatalogEntry | undefined): { key: string; value: string }[] {
    if (!tool?.parameters?.properties) return [];
    return Object.entries(tool.parameters.properties).map(([key]) => ({
        key,
        value: '',
    }));
}

/**
 * Routines — saved, deterministic tool sequences the user runs on demand.
 * Describe in plain language → supervisor model drafts steps → review/edit → save.
 * Invocable from chat via run_saved_routine.
 */
export default function RoutinesWorkspace() {
    const cached = readCache<Routine[]>(CACHE_KEY);
    const [routines, setRoutines] = useState<Routine[]>(cached || []);
    const [loading, setLoading] = useState(!cached);
    const [running, setRunning] = useState<string | null>(null);
    const [draft, setDraft] = useState<DraftState | null>(null);
    const [jsonMode, setJsonMode] = useState(false);
    const [jsonText, setJsonText] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [nlPrompt, setNlPrompt] = useState('');
    const [drafting, setDrafting] = useState(false);
    const cachedCatalog = readCache<ToolCatalogEntry[]>('tool_catalog');
    const [toolCatalog, setToolCatalog] = useState<ToolCatalogEntry[]>(cachedCatalog || []);
    const [toolSearch, setToolSearch] = useState('');

    const fetchRoutines = useCallback(async () => {
        const api = window.pywebview?.api;
        const getter = api?.get_routines ?? api?.get_workflows;
        if (!getter) {
            setLoadError(new Error('Routines are unavailable on this client.'));
            setLoading(false);
            return;
        }
        setLoadError(null);
        try {
            const result = await getter();
            const list = Array.isArray(result) ? (result as Routine[]) : [];
            setRoutines(list);
            writeCache(CACHE_KEY, list);
        } catch (err) {
            // Surface it: rendering the empty state here claimed "no routines yet"
            // when the truth was "couldn't reach Chad".
            console.error('[Routines] Failed to fetch:', err);
            setLoadError(err);
        } finally {
            setLoading(false);
        }
    }, []);

    const fetchCatalog = useCallback(async () => {
        const api = window.pywebview?.api;
        if (!api?.list_tool_catalog) return;
        try {
            const result = await api.list_tool_catalog();
            if (Array.isArray(result)) {
                const list = result as ToolCatalogEntry[];
                setToolCatalog(list);
                writeCache('tool_catalog', list);
            }
        } catch (e) {
            console.warn('[Routines] tool catalog unavailable:', e);
        }
    }, []);

    const updateRoutines = (fn: (prev: Routine[]) => Routine[]) =>
        setRoutines((prev) => {
            const next = fn(prev);
            writeCache(CACHE_KEY, next);
            return next;
        });

    useEffect(() => {
        fetchRoutines();
        fetchCatalog();
    }, [fetchRoutines, fetchCatalog]);

    const toolsByName = useMemo(() => {
        const m = new Map<string, ToolCatalogEntry>();
        toolCatalog.forEach((t) => m.set(t.name, t));
        return m;
    }, [toolCatalog]);

    const filteredTools = useMemo(() => {
        const q = toolSearch.toLowerCase().trim();
        if (!q) return toolCatalog;
        return toolCatalog.filter(
            (t) =>
                t.name.toLowerCase().includes(q) ||
                t.description.toLowerCase().includes(q) ||
                t.category.toLowerCase().includes(q)
        );
    }, [toolCatalog, toolSearch]);

    const openCreate = () => {
        setError(null);
        setJsonMode(false);
        setNlPrompt('');
        setDraft(emptyDraft());
    };

    const openEdit = (r: Routine) => {
        setError(null);
        setJsonMode(false);
        setNlPrompt('');
        setDraft(routineToDraft(r));
    };

    const closeEditor = () => {
        setDraft(null);
        setError(null);
    };

    const patchDraft = (patch: Partial<DraftState>) => setDraft((d) => (d ? { ...d, ...patch } : d));

    const updateStep = (id: string, patch: Partial<DraftStep>) =>
        setDraft((d) =>
            d ? { ...d, steps: d.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)) } : d
        );

    const selectToolForStep = (stepId: string, toolName: string) => {
        const tool = toolsByName.get(toolName);
        const label = tool
            ? tool.description.split(/[.!]/)[0].slice(0, 60) || toolName
            : '';
        const schemaRows = schemaRowsForTool(tool);
        updateStep(stepId, {
            tool_name: toolName,
            label: label,
            params: schemaRows.length ? schemaRows : [],
            expanded: true,
        });
    };

    const addStep = () =>
        setDraft((d) =>
            d
                ? {
                      ...d,
                      steps: [
                          ...d.steps,
                          { id: uid(), tool_name: '', label: '', params: [], expanded: true },
                      ],
                  }
                : d
        );

    const removeStep = (id: string) =>
        setDraft((d) => (d ? { ...d, steps: d.steps.filter((s) => s.id !== id) } : d));

    const moveStep = (id: string, dir: -1 | 1) =>
        setDraft((d) => {
            if (!d) return d;
            const idx = d.steps.findIndex((s) => s.id === id);
            const next = idx + dir;
            if (idx < 0 || next < 0 || next >= d.steps.length) return d;
            const steps = [...d.steps];
            [steps[idx], steps[next]] = [steps[next], steps[idx]];
            return { ...d, steps };
        });

    const updateStepParams = (
        stepId: string,
        fn: (rows: { key: string; value: string }[]) => { key: string; value: string }[]
    ) =>
        setDraft((d) =>
            d
                ? { ...d, steps: d.steps.map((s) => (s.id === stepId ? { ...s, params: fn(s.params) } : s)) }
                : d
        );

    const addParam = (stepId: string) =>
        updateStepParams(stepId, (rows) => [...rows, { key: '', value: '' }]);
    const removeParam = (stepId: string, i: number) =>
        updateStepParams(stepId, (rows) => rows.filter((_, j) => j !== i));
    const patchParam = (stepId: string, i: number, patch: Partial<{ key: string; value: string }>) =>
        updateStepParams(stepId, (rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

    const handleDraftFromNL = async () => {
        const api = window.pywebview?.api;
        if (!api?.draft_routine || !nlPrompt.trim()) return;
        setDrafting(true);
        setError(null);
        try {
            const res = (await api.draft_routine(nlPrompt.trim())) as {
                success?: boolean;
                steps?: RoutineStep[];
                error?: string;
            };
            if (!res?.success || !res.steps?.length) {
                setError(res?.error || 'Could not draft routine steps.');
                return;
            }
            setDraft((d) =>
                d
                    ? {
                          ...d,
                          steps: res.steps!.map((s) => ({
                              id: uid(),
                              tool_name: s.tool_name,
                              label: s.label || s.tool_name,
                              params: paramsToRows(s.params || {}),
                              expanded: false,
                          })),
                      }
                    : d
            );
        } catch (e) {
            setError(String(e));
        } finally {
            setDrafting(false);
        }
    };

    const enterJsonMode = () => {
        if (!draft) return;
        const steps: RoutineStep[] = draft.steps.map((s) => ({
            tool_name: s.tool_name,
            label: s.label || undefined,
            params: rowsToParams(s.params),
        }));
        setJsonText(JSON.stringify(steps, null, 2));
        setJsonMode(true);
    };

    const exitJsonMode = () => {
        try {
            const parsed = JSON.parse(jsonText) as RoutineStep[];
            if (!Array.isArray(parsed)) throw new Error('Steps must be a JSON array.');
            setDraft((d) =>
                d
                    ? {
                          ...d,
                          steps: parsed.map((s) => ({
                              id: uid(),
                              tool_name: s.tool_name || '',
                              label: s.label || '',
                              params: paramsToRows(s.params || {}),
                              expanded: false,
                          })),
                      }
                    : d
            );
            setJsonMode(false);
            setError(null);
        } catch (e) {
            setError(`Invalid JSON: ${(e as Error).message}`);
        }
    };

    const handleSave = async () => {
        if (!draft) return;
        const name = draft.name.trim();
        if (!name) {
            setError('Name is required.');
            return;
        }

        let steps: RoutineStep[];
        if (jsonMode) {
            try {
                steps = JSON.parse(jsonText);
                if (!Array.isArray(steps)) throw new Error('Steps must be a JSON array.');
            } catch (e) {
                setError(`Invalid JSON: ${(e as Error).message}`);
                return;
            }
        } else {
            steps = draft.steps
                .filter((s) => s.tool_name.trim())
                .map((s) => ({
                    tool_name: s.tool_name.trim(),
                    label: s.label.trim() || undefined,
                    params: rowsToParams(s.params),
                }));
        }

        if (steps.length === 0) {
            setError('Add at least one step.');
            return;
        }

        setError(null);
        const api = window.pywebview?.api;
        const description = draft.description.trim() || null;
        try {
            if (draft.editingId) {
                const result = await api?.update_workflow?.(draft.editingId, name, description ?? '', steps) as
                    { success?: boolean } | undefined;
                if (!result?.success) {
                    setError('Could not save changes. Your original routine is unchanged.');
                    return;
                }
                const updated: Routine = { id: draft.editingId, name, description, steps };
                updateRoutines((prev) => prev.map((r) => r.id === updated.id ? updated : r));
            } else {
                const result = await api?.create_workflow?.(name, steps, description) as
                    { success?: boolean; workflow?: Routine } | undefined;
                if (!result?.success || !result.workflow) {
                    setError('Could not create routine. Your draft is still available.');
                    return;
                }
                const created = result.workflow;
                updateRoutines((prev) => [created, ...prev]);
            }
            closeEditor();
        } catch (err) {
            setError(`Could not save routine: ${String(err)}`);
        }
    };

    const handleDelete = async (id: string) => {
        updateRoutines((prev) => prev.filter((w) => w.id !== id));
        await window.pywebview?.api?.delete_workflow?.(id);
    };

    const handleRun = async (id: string) => {
        setRunning(id);
        try {
            await window.pywebview?.api?.run_workflow?.(id);
        } finally {
            setTimeout(() => setRunning(null), 1500);
        }
    };

    return (
        <WorkspacePane
            icon={ListOrdered}
            title="Routines"
            subtitle="Reusable sequences you run on demand — or ask Chad to run one by name in chat."
            actions={
                !draft && (
                    <button type="button" className="ws-btn" onClick={openCreate}>
                        <Plus size={15} /> New routine
                    </button>
                )
            }
        >
            <AnimatePresence mode="wait">
                {draft ? (
                    <motion.div
                        key="editor"
                        className="wf-editor"
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -8 }}
                        transition={{ duration: 0.18 }}
                    >
                        <div className="wf-editor__meta">
                            <input
                                className="wf-input wf-input--title"
                                value={draft.name}
                                onChange={(e) => patchDraft({ name: e.target.value })}
                                placeholder="Routine name"
                                autoFocus
                            />
                            <input
                                className="wf-input"
                                value={draft.description}
                                onChange={(e) => patchDraft({ description: e.target.value })}
                                placeholder="What does this do? (optional)"
                            />
                        </div>

                        <div className="wf-nl-draft">
                            <label className="wf-section-label">Describe it</label>
                            <p className="wf-nl-hint">
                                The supervisor drafts fixed tool steps. For routines that read assignments and choose files, use a skill in chat.
                            </p>
                            <textarea
                                className="wf-input wf-nl-textarea"
                                value={nlPrompt}
                                onChange={(e) => setNlPrompt(e.target.value)}
                                placeholder='e.g. "Open Chrome at https://example.com"'
                                rows={2}
                            />
                            <button
                                type="button"
                                className="ws-btn wf-nl-btn"
                                onClick={handleDraftFromNL}
                                disabled={drafting || !nlPrompt.trim()}
                            >
                                {drafting ? (
                                    <>
                                        <Loader2 size={14} className="animate-spin" /> Drafting…
                                    </>
                                ) : (
                                    <>
                                        <Sparkles size={14} /> Generate steps
                                    </>
                                )}
                            </button>
                        </div>

                        <div className="wf-editor__steps-head">
                            <span className="wf-section-label">Steps</span>
                            <button
                                type="button"
                                className="wf-json-toggle"
                                onClick={jsonMode ? exitJsonMode : enterJsonMode}
                                title="Toggle raw JSON editing"
                            >
                                <Code2 size={13} /> {jsonMode ? 'Visual editor' : 'Advanced (JSON)'}
                            </button>
                        </div>

                        {jsonMode ? (
                            <textarea
                                className="wf-json"
                                value={jsonText}
                                onChange={(e) => setJsonText(e.target.value)}
                                rows={14}
                                spellCheck={false}
                            />
                        ) : (
                            <div className="wf-steps">
                                {draft.steps.map((step, i) => {
                                    const tool = toolsByName.get(step.tool_name);
                                    const isExpanded = step.expanded ?? false;
                                    return (
                                        <div key={step.id} className="wf-step">
                                            <div className="wf-step__rail">
                                                <span className="wf-step__num">{i + 1}</span>
                                                {i < draft.steps.length - 1 && (
                                                    <span className="wf-step__line" />
                                                )}
                                            </div>
                                            <div className="wf-step__body">
                                                <button
                                                    type="button"
                                                    className="wf-step__summary"
                                                    onClick={() =>
                                                        updateStep(step.id, { expanded: !isExpanded })
                                                    }
                                                >
                                                    <span className="wf-step__summary-label">
                                                        {step.label || step.tool_name || 'Choose an action'}
                                                    </span>
                                                    {step.tool_name && (
                                                        <span className="wf-step__summary-tool">
                                                            {step.tool_name}
                                                        </span>
                                                    )}
                                                    <ChevronRight
                                                        size={14}
                                                        className={`wf-step__chevron ${isExpanded ? 'is-open' : ''}`}
                                                    />
                                                </button>

                                                {isExpanded && (
                                                    <div className="wf-step__detail">
                                                        <input
                                                            className="wf-input wf-input--search"
                                                            value={toolSearch}
                                                            onChange={(e) => setToolSearch(e.target.value)}
                                                            placeholder="Search tools…"
                                                        />
                                                        <div className="wf-tool-pick">
                                                            {filteredTools.slice(0, 40).map((t) => (
                                                                <button
                                                                    key={t.name}
                                                                    type="button"
                                                                    className={`wf-tool-opt ${step.tool_name === t.name ? 'is-selected' : ''}`}
                                                                    onClick={() => selectToolForStep(step.id, t.name)}
                                                                >
                                                                    <span className="wf-tool-opt__name">{t.name}</span>
                                                                    <span className="wf-tool-opt__cat">{t.category}</span>
                                                                </button>
                                                            ))}
                                                        </div>
                                                        <input
                                                            className="wf-input wf-input--label"
                                                            value={step.label}
                                                            onChange={(e) =>
                                                                updateStep(step.id, { label: e.target.value })
                                                            }
                                                            placeholder="Display label"
                                                        />
                                                        <div className="wf-params">
                                                            {step.params.map((p, pi) => (
                                                                <div key={pi} className="wf-param">
                                                                    <input
                                                                        className="wf-input wf-input--pkey"
                                                                        value={p.key}
                                                                        onChange={(e) =>
                                                                            patchParam(step.id, pi, {
                                                                                key: e.target.value,
                                                                            })
                                                                        }
                                                                        placeholder="param"
                                                                        readOnly={!!tool?.parameters?.properties?.[p.key]}
                                                                    />
                                                                    <input
                                                                        className="wf-input wf-input--pval"
                                                                        value={p.value}
                                                                        onChange={(e) =>
                                                                            patchParam(step.id, pi, {
                                                                                value: e.target.value,
                                                                            })
                                                                        }
                                                                        placeholder={
                                                                            tool?.parameters?.properties?.[p.key]
                                                                                ?.description || 'value'
                                                                        }
                                                                    />
                                                                    <button
                                                                        type="button"
                                                                        className="wf-param__remove"
                                                                        onClick={() => removeParam(step.id, pi)}
                                                                        title="Remove param"
                                                                    >
                                                                        <X size={13} />
                                                                    </button>
                                                                </div>
                                                            ))}
                                                            <button
                                                                type="button"
                                                                className="wf-param__add"
                                                                onClick={() => addParam(step.id)}
                                                            >
                                                                <Plus size={12} /> Add parameter
                                                            </button>
                                                        </div>
                                                        <div className="wf-step__controls wf-step__controls--inline">
                                                            <button
                                                                type="button"
                                                                onClick={() => moveStep(step.id, -1)}
                                                                disabled={i === 0}
                                                                title="Move up"
                                                            >
                                                                <ChevronUp size={14} />
                                                            </button>
                                                            <button
                                                                type="button"
                                                                onClick={() => moveStep(step.id, 1)}
                                                                disabled={i === draft.steps.length - 1}
                                                                title="Move down"
                                                            >
                                                                <ChevronDown size={14} />
                                                            </button>
                                                            <button
                                                                type="button"
                                                                className="wf-step__remove"
                                                                onClick={() => removeStep(step.id)}
                                                                title="Remove step"
                                                                disabled={draft.steps.length === 1}
                                                            >
                                                                <Trash2 size={14} />
                                                            </button>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    );
                                })}

                                <button type="button" className="wf-add-step" onClick={addStep}>
                                    <Plus size={14} /> Add step
                                </button>
                            </div>
                        )}

                        {error && <div className="wf-error">{error}</div>}

                        <div className="wf-editor__footer">
                            <button type="button" className="ws-btn" onClick={handleSave}>
                                {draft.editingId ? 'Save changes' : 'Create routine'}
                            </button>
                            <button type="button" className="ws-btn ws-btn--ghost" onClick={closeEditor}>
                                Cancel
                            </button>
                        </div>
                    </motion.div>
                ) : loading ? (
                    <motion.div key="loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                        <RoutinesSkeleton />
                    </motion.div>
                ) : loadError ? (
                    <motion.div key="load-error" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                        <ErrorState
                            title="Couldn't load routines"
                            error={loadError}
                            onRetry={() => { setLoading(true); fetchRoutines(); }}
                        />
                    </motion.div>
                ) : routines.length === 0 ? (
                    <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                        <EmptyState
                            icon={ListOrdered}
                            title="No routines yet"
                            hint="Describe what you want once — morning briefing, inbox triage, standup prep — then run it anytime or ask Chad in chat."
                            action={
                                <button type="button" className="ws-btn" onClick={openCreate} style={{ marginTop: 8 }}>
                                    <Plus size={15} /> New routine
                                </button>
                            }
                        />
                    </motion.div>
                ) : (
                    <motion.div key="list" className="wf-grid" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                        {routines.map((r) => (
                            <motion.div
                                key={r.id}
                                className="wf-card"
                                initial={{ opacity: 0, y: 8 }}
                                animate={{ opacity: 1, y: 0 }}
                            >
                                <div className="wf-card__head">
                                    <h3 className="wf-card__name">{r.name}</h3>
                                    <div className="wf-card__menu">
                                        <button type="button" onClick={() => openEdit(r)} title="Edit">
                                            <Pencil size={14} />
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => handleDelete(r.id)}
                                            title="Delete"
                                            className="wf-card__danger"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                </div>
                                {r.description && <p className="wf-card__desc">{r.description}</p>}
                                <div className="wf-card__steps">
                                    {(r.steps || []).slice(0, 5).map((s, i) => (
                                        <span key={i} className="wf-pill" title={s.label || s.tool_name}>
                                            {s.label || s.tool_name || 'step'}
                                        </span>
                                    ))}
                                    {(r.steps?.length || 0) > 5 && (
                                        <span className="wf-pill wf-pill--more">
                                            +{(r.steps?.length || 0) - 5}
                                        </span>
                                    )}
                                </div>
                                <button
                                    type="button"
                                    className="wf-card__run"
                                    onClick={() => handleRun(r.id)}
                                    disabled={running === r.id}
                                >
                                    {running === r.id ? (
                                        <>
                                            <Loader2 size={14} className="animate-spin" /> Running…
                                        </>
                                    ) : (
                                        <>
                                            <Play size={14} /> Run
                                        </>
                                    )}
                                </button>
                            </motion.div>
                        ))}
                    </motion.div>
                )}
            </AnimatePresence>
        </WorkspacePane>
    );
}
