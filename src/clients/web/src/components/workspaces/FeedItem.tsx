import { useState } from 'react';
import { ExternalLink, Info, MessageSquare, X } from 'lucide-react';
import MarkdownMessage from '../data-display/MarkdownMessage';
import { useAppStore } from '../../store';

export interface FeedEntry {
    id: string; kind: string; title: string; body: string; created_at: string;
    status: string; metadata?: Record<string, unknown>;
}

function safeUrl(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; }
    catch { return null; }
}

export default function FeedItem({ entry, onDismiss }: { entry: FeedEntry; onDismiss: (id: string) => Promise<void> }) {
    const navigate = useAppStore(s => s.setActiveWorkspace);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(false);
    const [showReason, setShowReason] = useState(false);
    const [failedImage, setFailedImage] = useState<string | null>(null);
    const url = safeUrl(entry.metadata?.url);
    const image = safeUrl(entry.metadata?.image_url ?? entry.metadata?.thumbnail_url ?? entry.metadata?.cover_image);
    const category = typeof entry.metadata?.category === 'string' ? entry.metadata.category : null;
    const reason = typeof entry.metadata?.selection_reason === 'string' ? entry.metadata.selection_reason
        : category ? `Selected for the ${category.replaceAll('_', ' ')} category in your interest profile.`
            : 'Chad surfaced this discovery. No specific interest match was saved with it.';
    const provenance = entry.metadata?.content_type === 'source_summary' ? 'Chad summary'
        : entry.metadata?.content_type === 'source_excerpt' ? 'Source excerpt' : 'Source preview';
    const discuss = () => {
        navigate('chat');
        window.dispatchEvent(new CustomEvent('chad:compose-feed', { detail: `Let's explore this ${entry.kind === 'discovery' ? 'find' : 'update'}: ${entry.title}\n${url ?? ''}\n\n${entry.body}\n\nExplain what matters and how it connects to my interests or current projects.` }));
    };
    return <article className={`feed-item feed-item--${entry.kind}`}>
        <div className="feed-item__meta"><span>{entry.kind === 'discovery' ? 'For you' : entry.kind === 'action' ? 'Suggested next step' : entry.kind === 'thought' ? 'Connection' : 'Update'}{category ? ` / ${category}` : ''}</span><time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}</time></div>
        <div className="feed-item__heading"><h2>{entry.title}</h2><button className="feed-item__info" type="button" aria-label={`Why Chad picked ${entry.title}`} aria-expanded={showReason} aria-controls={`feed-reason-${entry.id}`} onClick={() => setShowReason(value => !value)}><Info size={16} /></button></div>
        {showReason && <aside id={`feed-reason-${entry.id}`} className="feed-item__reason"><strong>Why this?</strong> {reason}</aside>}
        <div className="feed-item__body"><MarkdownMessage content={entry.body} /></div>
        {image && failedImage !== image && <a className="feed-item__image-link" href={url ?? image} target="_blank" rel="noopener noreferrer" aria-label={`View image for ${entry.title}`}><img className="feed-item__image" src={image} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailedImage(image)} /></a>}
        {url && <p className="feed-item__source">{provenance} · {new URL(url).hostname}</p>}
        <div className="feed-item__actions">
            {url && <a className="ws-btn" href={url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> Read original</a>}
            <button className="ws-btn" type="button" onClick={discuss}><MessageSquare size={14} /> Discuss</button>
            <button className="ws-btn" type="button" disabled={busy} aria-label={`Dismiss ${entry.title}`} onClick={async () => { setBusy(true); setError(false); try { await onDismiss(String(entry.id)); } catch { setError(true); } finally { setBusy(false); } }}><X size={14} /> Dismiss</button>
        </div>
        {error && <p role="alert">Couldn't dismiss this item. Try again.</p>}
    </article>;
}
