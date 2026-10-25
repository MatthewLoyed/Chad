import { useCallback, useEffect, useState } from 'react';
import { Rss, RefreshCw } from 'lucide-react';
import WorkspacePane from './WorkspacePane';
import { EmptyState, ErrorState, SkeletonState } from '../common/AsyncState';
import { readCache, writeCache } from '../../utils/localCache';
import type { AutonomousRun } from '../modals/settings/AutomationsTab';
import { useAppStore } from '../../store';
import '../modals/settings/AutomationsTab.css';
import './FeedWorkspace.css';
import FeedItem, { type FeedEntry } from './FeedItem';

const CACHE_KEY = 'automations_summary';
type Summary = { recent_runs?: AutonomousRun[] };

/** Finds and background results from existing producers; never schedules work. */
export default function FeedWorkspace() {
    const active = useAppStore((s) => s.activeWorkspace === 'feed');
    const apiReady = useAppStore((s) => s.apiReady);
    const [summary, setSummary] = useState(() => readCache<Summary>(CACHE_KEY));
    const [loading, setLoading] = useState(!summary);
    const [error, setError] = useState<unknown>(null);
    const [showQuiet, setShowQuiet] = useState(false);
    const [entries, setEntries] = useState(() => readCache<FeedEntry[]>('dashboard_feed') ?? []);
    const [view, setView] = useState<'all' | 'reads' | 'activity'>('all');
    const [feedError, setFeedError] = useState<unknown>(null);
    const refreshEntries = useCallback(async () => {
        const getter = window.pywebview?.api?.get_dashboard_feed;
        if (!getter) return;
        try {
            const result = await getter(50);
            if (!Array.isArray(result)) throw new Error('Feed items are unavailable.');
            setEntries(result as FeedEntry[]);
            writeCache('dashboard_feed', result);
            setFeedError(null);
        } catch (err) { setFeedError(err); }
    }, []);
    const dismissEntry = async (id: string) => {
        const api = window.pywebview?.api;
        if (!api?.dismiss_dashboard_entry) throw new Error('Dismiss is unavailable.');
        const result = await api.dismiss_dashboard_entry(id) as { success?: boolean } | null;
        if (!result || result.success === false) throw new Error('Dismiss failed.');
        setEntries(current => {
            const next = current.filter(entry => String(entry.id) !== id);
            writeCache('dashboard_feed', next);
            return next;
        });
    };
    const refresh = useCallback(async () => {
        setLoading(true);
        await refreshEntries();
        try {
            const getter = window.pywebview?.api?.get_automations_summary;
            if (!getter) throw new Error('Autonomous activity is unavailable on this client.');
            const result = await getter() as Summary | null;
            if (!result) throw new Error('Autonomous activity is unavailable.');
            setSummary(result);
            writeCache(CACHE_KEY, result);
            setError(null);
        } catch (err) { setError(err); }
        finally { setLoading(false); }
    }, [refreshEntries]);

    useEffect(() => { if (active && apiReady) void refresh(); }, [active, apiReady, refresh]);
    const runs = [...(summary?.recent_runs ?? [])]
        .filter((run) => showQuiet || !run.silent)
        .sort((a, b) => new Date(b.ran_at).getTime() - new Date(a.ran_at).getTime());
    const quietCount = (summary?.recent_runs ?? []).filter((run) => run.silent).length;

    return (
        <WorkspacePane icon={Rss} title="Feed" subtitle="Quick finds for your interests. Scroll, read, or discuss with Chad."
            actions={<button type="button" className="ws-btn" disabled={loading} onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</button>}>
            <div className="activity-feed">
                <nav className="feed-tabs" aria-label="Feed views">{([['all', 'All'], ['reads', 'For you'], ['activity', 'Activity']] as const).map(([key, label]) => <button type="button" className="ws-btn" aria-pressed={view === key} key={key} onClick={() => setView(key)}>{label}</button>)}</nav>
                {feedError != null && <ErrorState title="Couldn't refresh finds" error={feedError} onRetry={refreshEntries} />}
                {view !== 'activity' && entries.filter(entry => entry.status === 'active' && (view !== 'reads' || entry.kind === 'discovery')).map(entry => <FeedItem key={entry.id} entry={entry} onDismiss={dismissEntry} />)}
                {view === 'reads' && !entries.some(entry => entry.status === 'active' && entry.kind === 'discovery') && <EmptyState icon={Rss} title="Your next good read is on its way" hint="Chad's discovery checks bring in articles and finds based on your interest profile. Manage background checks in Automations." />}
                {view !== 'reads' && <>
                <h2 className="feed-section-title">Background activity</h2>
                <label className="activity-feed__filter"><input type="checkbox" checked={showQuiet} onChange={(e) => setShowQuiet(e.target.checked)} /> Show quiet checks{quietCount > 0 ? ` (${quietCount})` : ''}</label>
                {error != null && <ErrorState title="Couldn't refresh activity" error={error} onRetry={refresh} />}
                {loading && !summary ? <SkeletonState count={3} lines={2} /> : summary && (
                    runs.length === 0 ? <EmptyState icon={Rss} title="You're caught up" hint={quietCount ? 'Background checks were all clear. Enable quiet checks to see them.' : 'Updates will appear after Chad runs a scheduled task or background check.'} /> : (
                        <div className="auto-run-list">
                            {runs.map((run) => (
                                <article key={run.id} className={`auto-run-card ${run.silent ? 'auto-run-card--silent' : ''}`}>
                                    <div className="auto-run-card__head">
                                        <span className="auto-run-card__source">{run.source.replaceAll('_', ' ')}</span>
                                        <span className="auto-run-card__outcome">{run.silent ? 'All clear' : run.outcome.replaceAll('_', ' ')}</span>
                                        <time className="auto-run-card__time" dateTime={run.ran_at}>{new Date(run.ran_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</time>
                                    </div>
                                    <p className="auto-run-card__summary">{run.summary}</p>
                                </article>
                            ))}
                        </div>
                    )
                )}
                </>}
            </div>
        </WorkspacePane>
    );
}
