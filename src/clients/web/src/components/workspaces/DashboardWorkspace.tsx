import { useEffect, useState, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
    LayoutDashboard, RefreshCw, Clock, Heart, Brain, Activity,
    Compass, Zap, X, Loader2, CheckCircle2, Bot,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import MarkdownMessage from '../data-display/MarkdownMessage';
import WorkspacePane from './WorkspacePane';
import DashboardLessonsCard from './DashboardLessonsCard';
import { ErrorState, EmptyState } from '../common/AsyncState';
import { readCache, writeCache } from '../../utils/localCache';
import './DashboardWorkspace.css';

type EntryKind = 'thought' | 'life' | 'status' | 'discovery' | 'action';

interface DashboardAction {
    type: 'tool' | 'fast_track';
    target: string;
    params?: Record<string, unknown>;
    label: string;
}

interface DashboardEntry {
    id: string;
    kind: EntryKind;
    title: string;
    body: string;
    metadata?: Record<string, unknown>;
    action?: DashboardAction | null;
    status: 'active' | 'done' | 'dismissed' | 'expired';
    pinned: boolean;
    created_at: string;
}

const KIND_META: Record<EntryKind, { icon: LucideIcon; label: string }> = {
    thought: { icon: Brain, label: 'Thought' },
    life: { icon: Heart, label: 'Life' },
    status: { icon: Activity, label: 'Status' },
    discovery: { icon: Compass, label: 'Discovery' },
    action: { icon: Zap, label: 'Action' },
};

function daysUntil(targetDate: string): number | null {
    const target = new Date(targetDate);
    if (Number.isNaN(target.getTime())) return null;
    const now = new Date();
    const ms = target.setHours(0, 0, 0, 0) - now.setHours(0, 0, 0, 0);
    return Math.round(ms / 86_400_000);
}

function PinnedWidget({
    entry, onDismiss,
}: {
    entry: DashboardEntry;
    onDismiss: (id: string) => void;
}) {
    const targetDate = entry.metadata?.target_date as string | undefined;
    const days = targetDate ? daysUntil(targetDate) : null;
    const { icon: Icon } = KIND_META[entry.kind];
    return (
        <div className="dash-pinned-card">
            <Icon size={16} className="dash-pinned-card__icon" />
            <div className="dash-pinned-card__body">
                <span className="dash-pinned-card__title">{entry.title}</span>
                {days !== null ? (
                    <span className="dash-pinned-card__metric">
                        {days > 0 ? `${days} day${days === 1 ? '' : 's'} to go` : days === 0 ? 'Today!' : `${Math.abs(days)} days ago`}
                    </span>
                ) : (
                    <span className="dash-pinned-card__metric">{entry.body}</span>
                )}
            </div>
            <button
                type="button"
                className="dash-pinned-card__dismiss"
                onClick={() => onDismiss(entry.id)}
                title="Dismiss"
                aria-label={`Dismiss ${entry.title}`}
            >
                <X size={13} />
            </button>
        </div>
    );
}

function FeedCard({
    entry, onRun, onDismiss, isRunning,
}: {
    entry: DashboardEntry;
    onRun: (id: string) => void;
    onDismiss: (id: string) => void;
    isRunning: boolean;
}) {
    const { icon: Icon, label } = KIND_META[entry.kind];
    const isDone = entry.status === 'done';
    const isAuto = entry.metadata?.auto_run === true;
    return (
        <motion.div
            className={`dash-card dash-card--${entry.kind}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
        >
            <div className="dash-card__head">
                <span className={`dash-card__badge dash-card__badge--${entry.kind}`}>
                    <Icon size={12} strokeWidth={2} /> {label}
                </span>
                {isAuto && (
                    <span className="dash-card__auto-tag" title="Written by an autonomous run">
                        <Bot size={10} /> auto
                    </span>
                )}
                <span className="dash-card__title">{entry.title}</span>
                <button
                    type="button"
                    className="dash-card__dismiss"
                    onClick={() => onDismiss(entry.id)}
                    title="Dismiss"
                >
                    <X size={13} />
                </button>
            </div>
            {entry.body && (
                <div className="dash-card__body prose">
                    <MarkdownMessage content={entry.body} />
                </div>
            )}
            {entry.kind === 'action' && entry.action && (
                <div className="dash-card__action">
                    {isDone ? (
                        <span className="dash-card__done">
                            <CheckCircle2 size={14} /> Done
                        </span>
                    ) : (
                        <motion.button
                            type="button"
                            className="dash-card__cta"
                            onClick={() => onRun(entry.id)}
                            disabled={isRunning}
                            whileHover={{ scale: 1.03 }}
                            whileTap={{ scale: 0.97 }}
                        >
                            {isRunning ? <Loader2 size={14} className="dash-card__spinner" /> : <Zap size={14} />}
                            {entry.action.label || 'Run it'}
                        </motion.button>
                    )}
                </div>
            )}
        </motion.div>
    );
}

type FeedFilter = 'all' | 'autonomous' | 'actions';

const CACHE_KEY = 'dashboard_feed';

export default function DashboardWorkspace() {
    const cached = readCache<DashboardEntry[]>(CACHE_KEY);
    const [entries, setEntries] = useState<DashboardEntry[]>(cached || []);
    const [lastUpdated, setLastUpdated] = useState<string>('');
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [runningIds, setRunningIds] = useState<Set<string>>(new Set());
    const [filter, setFilter] = useState<FeedFilter>('all');

    const fetchFeed = useCallback(async () => {
        if (!window.pywebview?.api?.get_dashboard_feed) {
            setLoadError(new Error('The dashboard is unavailable on this client.'));
            return;
        }
        setIsRefreshing(true);
        try {
            const data = await window.pywebview.api.get_dashboard_feed(50);
            if (Array.isArray(data)) {
                const list = data as DashboardEntry[];
                setEntries(list);
                writeCache(CACHE_KEY, list);
            }
            setLastUpdated(new Date().toLocaleTimeString());
            setLoadError(null);
        } catch (err) {
            // Without this the feed falls back to "Nothing here yet", which reads
            // as "Chad had no thoughts" rather than "the feed failed to load".
            console.error('Failed to load dashboard feed', err);
            setLoadError(err);
        } finally {
            setIsRefreshing(false);
        }
    }, []);

    useEffect(() => {
        fetchFeed();
        const interval = setInterval(fetchFeed, 30000);
        return () => clearInterval(interval);
    }, [fetchFeed]);

    const runAction = useCallback(async (id: string) => {
        const api = window.pywebview?.api;
        if (!api?.run_dashboard_action) return;
        setRunningIds(prev => new Set(prev).add(id));
        try {
            await api.run_dashboard_action(id);
            setEntries(prev => {
                const next = prev.map(e => (e.id === id ? { ...e, status: 'done' as const } : e));
                writeCache(CACHE_KEY, next);
                return next;
            });
        } catch (err) {
            console.error('Dashboard action failed', err);
        } finally {
            setRunningIds(prev => {
                const next = new Set(prev);
                next.delete(id);
                return next;
            });
        }
    }, []);

    const dismissEntry = useCallback(async (id: string) => {
        setEntries(prev => {
            const next = prev.filter(e => e.id !== id);
            writeCache(CACHE_KEY, next);
            return next;
        });
        const api = window.pywebview?.api;
        if (api?.dismiss_dashboard_entry) {
            api.dismiss_dashboard_entry(id).catch(console.error);
        }
    }, []);

    const pinned = entries.filter(e => e.pinned);
    const allFeed = entries.filter(e => !e.pinned);
    const feed = allFeed.filter(e => {
        if (filter === 'autonomous') return e.metadata?.auto_run === true;
        if (filter === 'actions') return e.kind === 'action';
        return true;
    });

    const autoCount = allFeed.filter(e => e.metadata?.auto_run === true).length;
    const actionCount = allFeed.filter(e => e.kind === 'action').length;

    return (
        <WorkspacePane
            icon={LayoutDashboard}
            title="Dashboard"
            subtitle="Chad's living feed — thoughts, status, finds, and proposed actions."
            actions={
                <>
                    {lastUpdated && (
                        <span className="dash-updated">
                            <Clock size={13} /> {lastUpdated}
                        </span>
                    )}
                    <button
                        type="button"
                        className="ws-btn ws-btn--ghost"
                        onClick={fetchFeed}
                        disabled={isRefreshing}
                        title="Refresh"
                    >
                        <RefreshCw size={14} className={isRefreshing ? 'animate-spin' : ''} />
                        Refresh
                    </button>
                </>
            }
        >
            <DashboardLessonsCard />

            {allFeed.length > 0 && (
                <div className="dash-filter-bar">
                    {([['all', 'All', allFeed.length], ['autonomous', 'Autonomous', autoCount], ['actions', 'Actions', actionCount]] as const).map(
                        ([key, label, count]) => (
                            <button
                                key={key}
                                type="button"
                                className={`dash-filter-btn ${filter === key ? 'dash-filter-btn--active' : ''}`}
                                onClick={() => setFilter(key)}
                            >
                                {label}
                                {count > 0 && <span className="dash-filter-btn__count">{count}</span>}
                            </button>
                        )
                    )}
                </div>
            )}

            {pinned.length > 0 && (
                <div className="dash-pinned-strip">
                    {pinned.map(entry => (
                        <PinnedWidget key={entry.id} entry={entry} onDismiss={dismissEntry} />
                    ))}
                </div>
            )}

            {feed.length === 0 && loadError ? (
                <ErrorState
                    title="Couldn't load the feed"
                    error={loadError}
                    onRetry={fetchFeed}
                    retrying={isRefreshing}
                />
            ) : feed.length === 0 ? (
                <EmptyState
                    icon={LayoutDashboard}
                    title="Nothing here yet"
                    hint="Chad will fill this in as he notices things worth keeping — thoughts, status updates, finds, and proposed actions."
                />
            ) : (
                <div className="dash-feed">
                    {feed.map(entry => (
                        <FeedCard
                            key={entry.id}
                            entry={entry}
                            onRun={runAction}
                            onDismiss={dismissEntry}
                            isRunning={runningIds.has(entry.id)}
                        />
                    ))}
                </div>
            )}
        </WorkspacePane>
    );
}
