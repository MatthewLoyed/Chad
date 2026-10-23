import { useCallback, useEffect, useState } from 'react';
import { Rss, RefreshCw } from 'lucide-react';
import WorkspacePane from './WorkspacePane';
import { EmptyState, ErrorState, SkeletonState } from '../common/AsyncState';
import { readCache, writeCache } from '../../utils/localCache';
import type { AutonomousRun } from '../modals/settings/AutomationsTab';
import { useAppStore } from '../../store';
import '../modals/settings/AutomationsTab.css';
import './FeedWorkspace.css';

const CACHE_KEY = 'automations_summary';
type Summary = { recent_runs?: AutonomousRun[] };

/** A readable view of the existing autonomous run log; never schedules work. */
export default function FeedWorkspace() {
    const active = useAppStore((s) => s.activeWorkspace === 'feed');
    const apiReady = useAppStore((s) => s.apiReady);
    const [summary, setSummary] = useState(() => readCache<Summary>(CACHE_KEY));
    const [loading, setLoading] = useState(!summary);
    const [error, setError] = useState<unknown>(null);
    const [showQuiet, setShowQuiet] = useState(false);
    const refresh = useCallback(async () => {
        setLoading(true);
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
    }, []);

    useEffect(() => { if (active && apiReady) void refresh(); }, [active, apiReady, refresh]);
    const runs = [...(summary?.recent_runs ?? [])]
        .filter((run) => showQuiet || !run.silent)
        .sort((a, b) => new Date(b.ran_at).getTime() - new Date(a.ran_at).getTime());
    const quietCount = (summary?.recent_runs ?? []).filter((run) => run.silent).length;

    return (
        <WorkspacePane icon={Rss} title="Feed" subtitle="Updates from Chad's background work. Check Automations to manage what runs."
            actions={<button type="button" className="ws-btn" disabled={loading} onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</button>}>
            <div className="activity-feed">
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
            </div>
        </WorkspacePane>
    );
}
