import { useState, useEffect, useCallback } from 'react';
import {
    Trash2, Pause, Play, Mail, Send, Github, Calendar, CheckCircle2, XCircle,
    Plus, ChevronUp, Zap, Clock, RotateCcw, Database, Brain, History, Server,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import Dropdown from '../../common/Dropdown';
import { SkeletonState, ErrorState } from '../../common/AsyncState';
import { readCache, writeCache } from '../../../utils/localCache';
import './AutomationsTab.css';

interface HeartbeatStatus {
    status: 'online' | 'offline';
    interval_minutes: number;
    last_check: string | null;
    checklist: string;
}

interface WebhookStatus {
    configured: boolean;
    last_triggered_at: string | null;
}

interface DaemonInfo {
    id: string;
    label: string;
    kind: 'llm' | 'sync' | 'webhook' | 'system';
    description: string;
    enabled: boolean;
    running: boolean;
    interval_minutes: number | null;
    interval_unit?: 'minutes' | 'hours';
    last_run: string | null;
    metadata?: Record<string, unknown>;
}

export interface AutonomousRun {
    id: string;
    source: string;
    outcome: string;
    summary: string;
    tools: string[];
    duration_sec: number | null;
    silent: boolean;
    ran_at: string;
}

interface AutomationsSummary {
    autonomous_actions_enabled?: boolean;
    autonomous_actions_locked?: boolean;
    heartbeat: HeartbeatStatus;
    cron: { task_count: number; total_count: number };
    webhooks: Record<string, WebhookStatus>;
    daemons?: DaemonInfo[];
    recent_runs?: AutonomousRun[];
}

interface CronJob {
    task_id: string;
    name: string;
    description: string;
    trigger: 'cron' | 'at' | 'every';
    status: string;
    schedule: string | null;
    last_run: string | null;
    next_run: string | null;
    is_agentic: boolean;
    execution_count: number;
}

const WEBHOOK_META: Record<string, { icon: typeof Mail; label: string }> = {
    gmail: { icon: Mail, label: 'Gmail' },
    telegram: { icon: Send, label: 'Telegram' },
    github: { icon: Github, label: 'GitHub' },
    calendar: { icon: Calendar, label: 'Calendar' },
};

const SOURCE_LABELS: Record<string, string> = {
    heartbeat: 'Heartbeat',
    cron: 'Scheduled',
    ingestion_sync: 'Index sync',
    webhook: 'Webhook',
    reflection: 'Reflection',
    skill: 'Skill',
    system: 'System',
};

const KIND_META: Record<string, { icon: LucideIcon; label: string }> = {
    llm: { icon: Brain, label: 'LLM' },
    sync: { icon: Database, label: 'Sync' },
    webhook: { icon: Send, label: 'Event' },
    system: { icon: Server, label: 'System' },
};

interface SchedulePreset {
    label: string;
    sublabel: string;
    value: string;
    trigger: string;
}

const SCHEDULE_PRESETS: SchedulePreset[] = [
    { label: 'Every hour', sublabel: 'on the hour', value: '0 * * * *', trigger: 'cron' },
    { label: 'Daily 9 AM', sublabel: 'every morning', value: '0 9 * * *', trigger: 'cron' },
    { label: 'Daily 6 PM', sublabel: 'every evening', value: '0 18 * * *', trigger: 'cron' },
    { label: 'Weekdays', sublabel: 'Mon–Fri 9 AM', value: '0 9 * * 1-5', trigger: 'cron' },
    { label: 'Weekly', sublabel: 'Monday 9 AM', value: '0 9 * * 1', trigger: 'cron' },
    { label: 'Custom', sublabel: 'raw expression', value: '', trigger: 'cron' },
];

const TRIGGER_OPTIONS = [
    { value: 'cron', label: 'Cron expression' },
    { value: 'every', label: 'Interval (e.g. 30m, 2h)' },
    { value: 'at', label: 'One-time (ISO datetime)' },
];

const TRIGGER_PLACEHOLDER: Record<string, string> = {
    cron: '0 9 * * *',
    every: '30m',
    at: '2026-07-01T09:00:00',
};

function formatRelative(iso: string | null): string {
    if (!iso) return 'never';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return 'never';
    const diffMs = Date.now() - date.getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
}

function formatNextRun(iso: string | null): string {
    if (!iso) return 'not scheduled';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return 'not scheduled';
    const diffMs = date.getTime() - Date.now();
    if (diffMs < 0) return 'overdue';
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `in ${mins}m`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `in ${hours}h`;
    return `in ${Math.round(hours / 24)}d`;
}

const CACHE_KEY_SUMMARY = 'automations_summary';
const CACHE_KEY_CRON = 'automations_cron_jobs';

export default function AutomationsTab() {
    const cachedSummary = readCache<AutomationsSummary>(CACHE_KEY_SUMMARY);
    const cachedCronJobs = readCache<CronJob[]>(CACHE_KEY_CRON);

    const [summary, setSummary] = useState<AutomationsSummary | null>(cachedSummary);
    const [cronJobs, setCronJobs] = useState<CronJob[]>(cachedCronJobs || []);
    const [daemons, setDaemons] = useState<DaemonInfo[]>(cachedSummary?.daemons ?? []);
    const [recentRuns, setRecentRuns] = useState<AutonomousRun[]>(cachedSummary?.recent_runs ?? []);
    const [checklistDraft, setChecklistDraft] = useState(cachedSummary?.heartbeat?.checklist ?? '');
    const [checklistSaving, setChecklistSaving] = useState(false);
    const [checklistDirty, setChecklistDirty] = useState(false);
    const [daemonSaving, setDaemonSaving] = useState<string | null>(null);
    const [daemonError, setDaemonError] = useState('');
    const [loading, setLoading] = useState(!cachedSummary);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [showCreateForm, setShowCreateForm] = useState(false);

    const [newName, setNewName] = useState('');
    const [selectedPreset, setSelectedPreset] = useState(1);
    const [newSchedule, setNewSchedule] = useState(SCHEDULE_PRESETS[1].value);
    const [newTrigger, setNewTrigger] = useState(SCHEDULE_PRESETS[1].trigger);
    const [newPrompt, setNewPrompt] = useState('');
    const [newAgentic, setNewAgentic] = useState(true);
    const [creating, setCreating] = useState(false);
    const [createError, setCreateError] = useState('');

    const isCustom = selectedPreset === SCHEDULE_PRESETS.length - 1;
    const activeWebhooks = Object.values(summary?.webhooks ?? {}).filter((w) => w.configured).length;

    const refresh = useCallback(async () => {
        const api = window.pywebview?.api;
        // Bail out AFTER clearing `loading` — returning first left this pane on
        // "Loading automations…" forever on any client missing the method.
        if (!api?.get_automations_summary) {
            setLoadError(new Error('Automations are unavailable on this client.'));
            setLoading(false);
            return;
        }
        if (!readCache(CACHE_KEY_SUMMARY)) {
            setLoading(true);
        }
        setLoadError(null);
        try {
            const [summaryRes, jobsRes] = await Promise.all([
                api.get_automations_summary(),
                api.list_cron_jobs ? api.list_cron_jobs() : Promise.resolve([]),
            ]);
            if (summaryRes) {
                const s = summaryRes as AutomationsSummary;
                setSummary(s);
                writeCache(CACHE_KEY_SUMMARY, s);
                if (!checklistDirty) {
                    setChecklistDraft(s.heartbeat?.checklist ?? '');
                }
                setDaemons(s.daemons ?? []);
                setRecentRuns(s.recent_runs ?? []);
            }
            if (Array.isArray(jobsRes)) {
                const jobs = jobsRes as CronJob[];
                setCronJobs(jobs);
                writeCache(CACHE_KEY_CRON, jobs);
            }
        } catch (err) {
            console.error('Failed to load automations summary', err);
            setLoadError(err);
        } finally {
            setLoading(false);
        }
    }, [checklistDirty]);

    useEffect(() => { refresh(); }, [refresh]);

    const updateDaemon = async (daemonId: string, patch: { enabled?: boolean; interval_minutes?: number }) => {
        const api = window.pywebview?.api;
        if (!api?.update_daemon_settings) return;
        setDaemonSaving(daemonId);
        setDaemonError('');
        try {
            const result = await api.update_daemon_settings(
                daemonId,
                patch.enabled,
                patch.interval_minutes,
            ) as { success?: boolean; error?: string } | null;
            if (!result?.success) throw new Error(result?.error || 'Could not save autonomous action settings.');
            await refresh();
        } catch (err) {
            console.error('Failed to update daemon settings', err);
            setDaemonError(err instanceof Error ? err.message : 'Could not save autonomous action settings.');
        } finally {
            setDaemonSaving(null);
        }
    };

    const saveChecklist = async () => {
        const api = window.pywebview?.api;
        if (!api?.update_heartbeat_checklist) return;
        setChecklistSaving(true);
        try {
            await api.update_heartbeat_checklist(checklistDraft);
            setChecklistDirty(false);
        } catch (err) {
            console.error('Failed to save heartbeat checklist', err);
        } finally {
            setChecklistSaving(false);
        }
    };

    const toggleCronStatus = async (job: CronJob) => {
        const api = window.pywebview?.api;
        if (!api?.set_cron_job_status) return;
        const nextStatus = job.status === 'paused' ? 'scheduled' : 'paused';
        setCronJobs((prev) => prev.map((j) => (j.task_id === job.task_id ? { ...j, status: nextStatus } : j)));
        try {
            await api.set_cron_job_status(job.task_id, nextStatus);
        } catch (err) {
            console.error('Failed to toggle cron job status', err);
            refresh();
        }
    };

    const deleteCronJob = async (taskId: string) => {
        const api = window.pywebview?.api;
        setCronJobs((prev) => prev.filter((j) => j.task_id !== taskId));
        if (api?.delete_cron_job) {
            api.delete_cron_job(taskId).catch(console.error);
        }
    };

    const handlePresetSelect = (idx: number) => {
        setSelectedPreset(idx);
        const preset = SCHEDULE_PRESETS[idx];
        if (preset.value) {
            setNewSchedule(preset.value);
            setNewTrigger(preset.trigger);
        } else {
            setNewSchedule('');
            setNewTrigger('cron');
        }
    };

    const createCronJob = async () => {
        const api = window.pywebview?.api;
        if (!api?.create_cron_job) return;
        setCreateError('');
        if (!newName.trim() || !newSchedule.trim() || !newPrompt.trim()) {
            setCreateError('Name, schedule, and prompt are all required.');
            return;
        }
        setCreating(true);
        try {
            const res = await api.create_cron_job(newName.trim(), newPrompt.trim(), newSchedule.trim(), newTrigger, newAgentic);
            if (res && (res as { success?: boolean }).success) {
                setNewName('');
                setSelectedPreset(1);
                setNewSchedule(SCHEDULE_PRESETS[1].value);
                setNewTrigger(SCHEDULE_PRESETS[1].trigger);
                setNewPrompt('');
                setShowCreateForm(false);
                await refresh();
            } else {
                setCreateError((res as { error?: string })?.error || 'Failed to create automation');
            }
        } catch (err) {
            setCreateError('Failed to create automation');
            console.error(err);
        } finally {
            setCreating(false);
        }
    };

    if (loading && !summary) {
        return <SkeletonState count={4} lines={2} />;
    }

    if (loadError && !summary) {
        return (
            <ErrorState title="Couldn't load automations" error={loadError} onRetry={refresh} />
        );
    }

    return (
        <div className="auto-page">
            <div className="auto-col">
                <section className="auto-card">
                    <header className="auto-card__header">
                        <Zap size={15} className="auto-card__header-icon" />
                        <span className="auto-card__title">Autonomous actions</span>
                        <span className={`auto-pill auto-pill--${summary?.autonomous_actions_enabled ? 'on' : 'off'}`}>
                            {summary?.autonomous_actions_enabled ? 'On' : 'Off'}
                        </span>
                        <label className="auto-daemon-toggle">
                            <input
                                type="checkbox"
                                role="switch"
                                aria-label="Autonomous actions"
                                checked={summary?.autonomous_actions_enabled ?? false}
                                disabled={daemonSaving !== null || loading || summary?.autonomous_actions_enabled === undefined || summary?.autonomous_actions_locked}
                                onChange={(e) => updateDaemon('master', { enabled: e.target.checked })}
                            />
                            <span className="auto-daemon-toggle__track" />
                        </label>
                    </header>
                    <div className="auto-card__body">
                        <p className="auto-hint">
                            Pause heartbeat, scheduled tasks, email triggers, reflection, and background sync together.
                            Manual chat stays available. Work already running may finish.
                            Turning this back on restores your individual settings.
                        </p>
                        {summary?.autonomous_actions_locked && <p className="auto-hint">Controlled by the server launch configuration.</p>}
                        {daemonError && <p role="alert">{daemonError}</p>}
                    </div>
                </section>
                <section className="auto-card">
                <header className="auto-card__header">
                    <Server size={15} className="auto-card__header-icon" />
                    <span className="auto-card__title">Background daemons</span>
                </header>
                <div className="auto-card__body">
                    <p className="auto-hint">
                        Everything Chad runs on a timer or in the background. Toggle and tune intervals here; changes save to your preferences.
                    </p>
                    <div className="auto-daemon-list">
                        {daemons.map((daemon) => {
                            const kind = KIND_META[daemon.kind] ?? KIND_META.system;
                            const KindIcon = kind.icon;
                            const unit = daemon.interval_unit ?? 'minutes';
                            const hasInterval = daemon.interval_minutes != null && ['heartbeat', 'ingestion_sync', 'reflection'].includes(daemon.id);
                            return (
                                <div key={daemon.id} className={`auto-daemon-card ${daemon.enabled ? 'auto-daemon-card--on' : ''}`}>
                                    <div className="auto-daemon-card__top">
                                        <KindIcon size={14} className="auto-daemon-card__icon" />
                                        <div className="auto-daemon-card__titles">
                                            <span className="auto-daemon-card__name">{daemon.label}</span>
                                            <span className="auto-daemon-card__kind">{kind.label}</span>
                                        </div>
                                        <label className="auto-daemon-toggle" title={daemon.enabled ? 'Disable' : 'Enable'}>
                                            <input
                                                type="checkbox"
                                                checked={daemon.enabled}
                                                disabled={daemonSaving !== null || !summary?.autonomous_actions_enabled || daemon.id === 'auto_update' || daemon.id === 'ingestion_sync'}
                                                onChange={(e) => updateDaemon(daemon.id, { enabled: e.target.checked })}
                                            />
                                            <span className="auto-daemon-toggle__track" />
                                        </label>
                                    </div>
                                    <p className="auto-daemon-card__desc">{daemon.description}</p>
                                    <div className="auto-daemon-card__meta">
                                        <span className={`auto-pill auto-pill--${daemon.running && daemon.enabled ? 'on' : 'off'}`}>
                                            {daemon.enabled ? (daemon.running ? 'Running' : 'Enabled') : 'Off'}
                                        </span>
                                        {daemon.last_run && (
                                            <span className="auto-stat">Last run {formatRelative(daemon.last_run)}</span>
                                        )}
                                        {hasInterval && (
                                            <span className="auto-stat">
                                                Every {daemon.interval_minutes} {unit === 'hours' ? 'h' : 'min'}
                                            </span>
                                        )}
                                    </div>
                                    {hasInterval && (
                                        <div className="auto-daemon-card__interval">
                                            <label className="auto-field-label">Interval ({unit})</label>
                                            <div className="auto-daemon-interval-row">
                                                <input
                                                    type="number"
                                                    className="auto-input auto-input--compact"
                                                    min={unit === 'hours' ? 1 : 5}
                                                    max={unit === 'hours' ? 168 : 1440}
                                                    defaultValue={daemon.interval_minutes ?? ''}
                                                    key={`${daemon.id}-${daemon.interval_minutes}`}
                                                    onBlur={(e) => {
                                                        const v = parseInt(e.target.value, 10);
                                                        if (!Number.isNaN(v) && v !== daemon.interval_minutes) {
                                                            updateDaemon(daemon.id, { interval_minutes: v });
                                                        }
                                                    }}
                                                />
                                            </div>
                                        </div>
                                    )}
                                    {daemon.id === 'auto_update' && (
                                        <p className="auto-daemon-card__note">Toggle in Settings → System.</p>
                                    )}
                                    {daemon.id === 'ingestion_sync' && (
                                        <p className="auto-daemon-card__note">Toggle in Settings → Capabilities. Interval only, here.</p>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            </section>

            <section className="auto-card">
                <header className="auto-card__header">
                    <Zap size={15} className="auto-card__header-icon" />
                    <span className="auto-card__title">Heartbeat</span>
                    <span className={`auto-pill auto-pill--${summary?.heartbeat?.status === 'online' ? 'on' : 'off'}`}>
                        {summary?.heartbeat?.status === 'online' ? 'Online' : 'Offline'}
                    </span>
                </header>
                <div className="auto-card__body">
                    <p className="auto-hint">
                        Chad wakes on a timer and runs this checklist. Every run is logged below — including silent all-clear cycles.
                    </p>
                    <div className="auto-stats">
                        <span className="auto-stat">
                            <Clock size={11} />
                            Every {summary?.heartbeat?.interval_minutes ?? 30} min
                        </span>
                        <span className="auto-stat">
                            Last check {formatRelative(summary?.heartbeat?.last_check ?? null)}
                        </span>
                    </div>
                    <textarea
                        className="auto-textarea"
                        value={checklistDraft}
                        onChange={(e) => { setChecklistDraft(e.target.value); setChecklistDirty(true); }}
                        rows={5}
                        placeholder={"# Heartbeat Checklist\n- Check Gmail for urgent unread emails\n- Return HEARTBEAT_OK if all clear"}
                    />
                    <div className="auto-card__footer">
                        <button
                            type="button"
                            className="ws-btn"
                            onClick={saveChecklist}
                            disabled={!checklistDirty || checklistSaving}
                        >
                            {checklistSaving ? 'Saving…' : 'Save checklist'}
                        </button>
                    </div>
                </div>
            </section>
            </div>

            <div className="auto-col">
            <section className="auto-card">
                <header className="auto-card__header">
                    <Send size={15} className="auto-card__header-icon" />
                    <span className="auto-card__title">Webhooks</span>
                    <span className="auto-card__badge">{activeWebhooks} active</span>
                </header>
                <div className="auto-card__body">
                    <p className="auto-hint">External events that wake Chad immediately — outside the heartbeat cycle.</p>
                    <div className="auto-webhook-grid">
                        {Object.entries(WEBHOOK_META).map(([service, { icon: Icon, label }]) => {
                            const status = summary?.webhooks?.[service];
                            const ok = status?.configured ?? false;
                            return (
                                <div key={service} className={`auto-webhook-card ${ok ? 'auto-webhook-card--active' : ''}`}>
                                    <div className="auto-webhook-card__top">
                                        <Icon size={15} className="auto-webhook-card__svc-icon" />
                                        <span className="auto-webhook-card__label">{label}</span>
                                        {ok ? (
                                            <CheckCircle2 size={13} className="auto-webhook-card__status-icon auto-webhook-card__status-icon--ok" />
                                        ) : (
                                            <XCircle size={13} className="auto-webhook-card__status-icon auto-webhook-card__status-icon--off" />
                                        )}
                                    </div>
                                    <span className="auto-webhook-card__sub">
                                        {ok ? `Last fired ${formatRelative(status!.last_triggered_at)}` : 'Not configured'}
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </section>

            <section className="auto-card">
                <header className="auto-card__header">
                    <RotateCcw size={15} className="auto-card__header-icon" />
                    <span className="auto-card__title">Scheduled</span>
                    {cronJobs.length > 0 && (
                        <span className="auto-card__badge">{cronJobs.length}</span>
                    )}
                    <button
                        type="button"
                        className="auto-add-btn"
                        onClick={() => setShowCreateForm(!showCreateForm)}
                        title={showCreateForm ? 'Cancel' : 'Add automation'}
                    >
                        {showCreateForm ? <ChevronUp size={14} /> : <><Plus size={13} /><span>Add</span></>}
                    </button>
                </header>
                <div className="auto-card__body">
                    <p className="auto-hint">Recurring or one-time tasks Chad runs on its own.</p>

                    {cronJobs.length === 0 && !showCreateForm ? (
                        <div className="auto-empty">
                            <span className="auto-empty__icon"><RotateCcw size={18} /></span>
                            <span className="auto-empty__title">No scheduled automations</span>
                            <span className="auto-empty__hint">Add a morning briefing, weekly review, or any recurring prompt.</span>
                            <button type="button" className="auto-add-btn" onClick={() => setShowCreateForm(true)}>
                                <Plus size={13} />
                                <span>Add automation</span>
                            </button>
                        </div>
                    ) : (
                        <div className="auto-job-list">
                            {cronJobs.map((job) => {
                                const isActive = job.status !== 'paused';
                                return (
                                    <div key={job.task_id} className={`auto-job-card ${!isActive ? 'auto-job-card--paused' : ''}`}>
                                        <div className={`auto-job-card__dot auto-job-card__dot--${isActive ? 'on' : 'off'}`} />
                                        <div className="auto-job-card__body">
                                            <div className="auto-job-card__name">{job.name}</div>
                                            <div className="auto-job-card__meta">
                                                <span className="auto-job-card__badge">{job.trigger}</span>
                                                {job.schedule && <span className="auto-job-card__schedule">{job.schedule}</span>}
                                                <span className="auto-job-card__timing">Next {formatNextRun(job.next_run)}</span>
                                                {job.execution_count > 0 && (
                                                    <span className="auto-job-card__runs">
                                                        {job.execution_count} run{job.execution_count !== 1 ? 's' : ''}
                                                    </span>
                                                )}
                                            </div>
                                            {job.last_run && (
                                                <div className="auto-job-card__last-run">
                                                    Last ran {formatRelative(job.last_run)}
                                                </div>
                                            )}
                                        </div>
                                        <div className="auto-job-card__actions">
                                            <button
                                                type="button"
                                                className="auto-icon-btn"
                                                onClick={() => toggleCronStatus(job)}
                                                title={isActive ? 'Pause' : 'Resume'}
                                            >
                                                {isActive ? <Pause size={14} /> : <Play size={14} />}
                                            </button>
                                            <button
                                                type="button"
                                                className="auto-icon-btn auto-icon-btn--danger"
                                                onClick={() => deleteCronJob(job.task_id)}
                                                title="Delete"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {showCreateForm && (
                        <div className="auto-create-form">
                            <div className="auto-create-form__header">New automation</div>

                            <label className="auto-field-label">Name</label>
                            <input
                                type="text"
                                className="auto-input"
                                placeholder="e.g. Morning briefing"
                                value={newName}
                                onChange={(e) => setNewName(e.target.value)}
                            />

                            <label className="auto-field-label">Schedule</label>
                            <div className="auto-preset-grid">
                                {SCHEDULE_PRESETS.map((preset, idx) => (
                                    <button
                                        key={preset.label}
                                        type="button"
                                        className={`auto-preset ${selectedPreset === idx ? 'auto-preset--active' : ''}`}
                                        onClick={() => handlePresetSelect(idx)}
                                    >
                                        <span className="auto-preset__label">{preset.label}</span>
                                        <span className="auto-preset__sub">{preset.sublabel}</span>
                                    </button>
                                ))}
                            </div>

                            {isCustom && (
                                <div className="auto-custom-schedule">
                                    <Dropdown
                                        value={newTrigger}
                                        options={TRIGGER_OPTIONS}
                                        onChange={setNewTrigger}
                                    />
                                    <input
                                        type="text"
                                        className="auto-input"
                                        placeholder={TRIGGER_PLACEHOLDER[newTrigger] ?? ''}
                                        value={newSchedule}
                                        onChange={(e) => setNewSchedule(e.target.value)}
                                    />
                                </div>
                            )}

                            <label className="auto-field-label">Prompt</label>
                            <textarea
                                className="auto-textarea"
                                rows={3}
                                placeholder="What should Chad do when this fires?"
                                value={newPrompt}
                                onChange={(e) => setNewPrompt(e.target.value)}
                            />

                            <label className="auto-checkbox">
                                <input type="checkbox" checked={newAgentic} onChange={(e) => setNewAgentic(e.target.checked)} />
                                Allow tool calls (uncheck for plain reminder/notification)
                            </label>

                            {createError && <p className="auto-error">{createError}</p>}

                            <div className="auto-create-form__actions">
                                <button
                                    type="button"
                                    className="ws-btn ws-btn--ghost"
                                    onClick={() => { setShowCreateForm(false); setCreateError(''); }}
                                >
                                    Cancel
                                </button>
                                <button
                                    type="button"
                                    className="ws-btn"
                                    onClick={createCronJob}
                                    disabled={creating}
                                >
                                    {creating ? 'Creating…' : 'Add automation'}
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            </section>

            <section className="auto-card">
                <header className="auto-card__header">
                    <History size={15} className="auto-card__header-icon" />
                    <span className="auto-card__title">Recent activity</span>
                    {recentRuns.length > 0 && (
                        <span className="auto-card__badge">{recentRuns.length}</span>
                    )}
                </header>
                <div className="auto-card__body">
                    <p className="auto-hint">
                        Durable log of autonomous runs (heartbeat, scheduled tasks, index sync, webhooks). Also surfaces on the Dashboard when action was taken.
                    </p>
                    {recentRuns.length === 0 ? (
                        <div className="auto-empty auto-empty--compact">
                            <span className="auto-empty__title">No runs logged yet</span>
                            <span className="auto-empty__hint">Activity appears here after the first heartbeat or sync cycle.</span>
                        </div>
                    ) : (
                        <div className="auto-run-list">
                            {recentRuns.map((run) => (
                                <div
                                    key={run.id}
                                    className={`auto-run-card ${run.silent ? 'auto-run-card--silent' : ''}`}
                                >
                                    <div className="auto-run-card__head">
                                        <span className="auto-run-card__source">
                                            {SOURCE_LABELS[run.source] ?? run.source}
                                        </span>
                                        <span className={`auto-run-card__outcome auto-run-card__outcome--${run.outcome}`}>
                                            {run.silent ? 'all clear' : run.outcome}
                                        </span>
                                        <span className="auto-run-card__time">{formatRelative(run.ran_at)}</span>
                                    </div>
                                    <p className="auto-run-card__summary">{run.summary}</p>
                                    {run.tools.length > 0 && (
                                        <div className="auto-run-card__tools">
                                            {run.tools.map((t) => (
                                                <span key={t} className="auto-run-card__tool">{t}</span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </section>
            </div>
        </div>
    );
}
