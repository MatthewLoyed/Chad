import { useState } from 'react';
import MarkdownMessage from '../data-display/MarkdownMessage';
import { THOUGHT_TEXT_PREVIEW_CHARS, truncateForPreview } from '../../utils/eventPruning';
import { repairStreamingMarkdown } from '../../utils/streamingMarkdown';
import './CollapsibleMarkdown.css';

interface CollapsibleMarkdownProps {
    content: string;
    previewChars?: number;
    /** When true, always show the full content (e.g. live streaming). */
    forceExpanded?: boolean;
    className?: string;
}

export default function CollapsibleMarkdown({
    content,
    previewChars = THOUGHT_TEXT_PREVIEW_CHARS,
    forceExpanded = false,
    className,
}: CollapsibleMarkdownProps) {
    const [expanded, setExpanded] = useState(false);
    const repaired = repairStreamingMarkdown(content);
    const isLong = repaired.length > previewChars;
    const showFull = forceExpanded || expanded || !isLong;
    const displayContent = showFull ? repaired : truncateForPreview(repaired, previewChars);

    return (
        <div className={className ? `collapsible-markdown ${className}` : 'collapsible-markdown'}>
            <MarkdownMessage content={displayContent} />
            {isLong && !forceExpanded && (
                <button
                    type="button"
                    className="collapsible-markdown__toggle"
                    onClick={(e) => {
                        e.stopPropagation();
                        setExpanded((prev) => !prev);
                    }}
                >
                    {expanded ? 'Show less' : 'Show more'}
                </button>
            )}
        </div>
    );
}
