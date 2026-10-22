import { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Globe } from 'lucide-react';
import './SourcesFooter.css';
import { collectTurnMedia, type SourceEntry } from '../../utils/sources';
import WebSearchMediaStrip from './WebSearchMediaStrip';

interface SourcesFooterProps {
    sources: SourceEntry[];
}

export default function SourcesFooter({ sources }: SourcesFooterProps) {
    const [expanded, setExpanded] = useState(false);
    const media = useMemo(() => collectTurnMedia(sources ?? []), [sources]);

    if (!sources || sources.length === 0) return null;

    const openSource = (url: string) => {
        window.open(url, '_blank', 'noopener,noreferrer');
    };

    return (
        <div className="sources-footer">
            <button
                type="button"
                className="sources-footer__toggle"
                onClick={() => setExpanded((v) => !v)}
                aria-expanded={expanded}
            >
                <Globe size={14} />
                <span>Sources ({sources.length})</span>
                {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {expanded && (
                <div className="sources-footer__list">
                    {media && <WebSearchMediaStrip media={media} />}
                    {sources.map((source) => (
                        <button
                            key={source.url}
                            type="button"
                            className={`sources-footer__row${source.success === false ? ' sources-footer__row--failed' : ''}`}
                            onClick={() => openSource(source.url)}
                            title={source.contentSource ? `Extracted via ${source.contentSource}` : source.url}
                        >
                            <Globe size={13} className="sources-footer__row-icon" />
                            <span className="sources-footer__row-title">{source.title}</span>
                            <span className="sources-footer__row-domain">{source.domain}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
