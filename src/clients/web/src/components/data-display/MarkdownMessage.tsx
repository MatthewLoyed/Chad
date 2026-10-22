import React, { useState, useMemo, lazy, Suspense } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Copy, Check, ChevronDown, ChevronUp } from 'lucide-react';
import './MarkdownMessage.css';
import YouTubePlayerCard from './YouTubePlayerCard';
import InlineLinkPill from './InlineLinkPill';
import { extractInlineCitations, type CitationTarget, type SourceEntry } from '../../utils/sources';

// Lazy-loaded so react-syntax-highlighter is fetched only when a code block
// actually renders, keeping it out of the first-paint bundle.
const CodeHighlighter = lazy(() => import('./CodeHighlighter'));

const COLLAPSE_LINE_THRESHOLD = 20;

function CodeBlock({ language, code }: { language: string; code: string }) {
    const [copied, setCopied] = useState(false);
    const lineCount = code.split('\n').length;
    const isLong = lineCount > COLLAPSE_LINE_THRESHOLD;
    const [collapsed, setCollapsed] = useState(isLong);

    const handleCopy = () => {
        navigator.clipboard.writeText(code);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="code-block-wrapper">
            <div className="code-block-header">
                <span className="code-language">{language}</span>
                <div className="code-block-actions">
                    {isLong && (
                        <button className="code-action-btn" onClick={() => setCollapsed(c => !c)} title={collapsed ? 'Expand' : 'Collapse'}>
                            {collapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                            {collapsed ? `${lineCount} lines` : 'Collapse'}
                        </button>
                    )}
                    <button className="code-action-btn code-copy-btn" onClick={handleCopy}>
                        {copied ? <Check size={12} /> : <Copy size={12} />}
                        {copied ? 'Copied!' : 'Copy'}
                    </button>
                </div>
            </div>
            {!collapsed && (
                <Suspense fallback={<pre className="code-block-fallback" style={{ margin: 0, borderRadius: '0 0 8px 8px' }}>{code}</pre>}>
                    <CodeHighlighter language={language} code={code} />
                </Suspense>
            )}
            {collapsed && (
                <div className="code-block-collapsed" onClick={() => setCollapsed(false)}>
                    {lineCount} lines hidden — click to expand
                </div>
            )}
        </div>
    );
}

interface MarkdownMessageProps {
    content: string;
    /** Sources for this turn (from extractSourcesFromThoughts / msg.sources), used to resolve
     *  inline [N] citation markers to a clickable/hoverable source. */
    sources?: SourceEntry[];
}

interface CodeProps extends React.HTMLAttributes<HTMLElement> {
    node?: unknown;
}

interface AnchorProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
    node?: unknown;
}

const CITATION_HREF_PREFIX = 'cite:';

function CitationMarker({ n, target }: { n: number; target: CitationTarget }) {
    return (
        <span className="citation-marker">
            <InlineLinkPill
                href={target.url}
                title={target.title}
                citationIndex={n}
                className="inline-citation-pill"
            >
                {target.domain}
            </InlineLinkPill>
        </span>
    );
}

const MarkdownMessage: React.FC<MarkdownMessageProps> = ({ content: rawContent, sources }) => {
    // Strip the trailing "## Sources" block (SourcesFooter already renders it) and resolve
    // inline [N] markers against it — see extractInlineCitations in utils/sources.ts.
    const { content, citations } = useMemo(() => {
        const { content: stripped, citations: parsed } = extractInlineCitations(rawContent, sources);
        if (parsed.size === 0) return { content: stripped, citations: parsed };
        // Turn bare "[N]" markers into real markdown links the `a` renderer can intercept —
        // only citation numbers we actually resolved, so unrelated "[1]"-looking text is untouched.
        const linked = stripped.replace(/\[(\d+)\](?!\()/g, (whole, numStr) => {
            const n = parseInt(numStr, 10);
            return parsed.has(n) ? `[${n}](${CITATION_HREF_PREFIX}${n})` : whole;
        });
        return { content: linked, citations: parsed };
    }, [rawContent, sources]);

    // Stable identity so react-markdown can reconcile across streaming re-renders
    // instead of treating the renderer map as new every token batch.
    const components = useMemo(() => ({
        code(props: CodeProps) {
            const { children, className, node: _node, ...rest } = props;
            void _node;
            const match = /language-(\w+)/.exec(className || '');
            return match ? (
                <CodeBlock language={match[1]} code={String(children).replace(/\n$/, '')} />
            ) : (
                <code {...rest} className={className ? className : 'inline-code'}>
                    {children}
                </code>
            );
        },
        a: ({ node: _node, ...props }: AnchorProps) => {
            void _node;
            const href = props.href || '';

            if (href.startsWith(CITATION_HREF_PREFIX)) {
                const n = parseInt(href.slice(CITATION_HREF_PREFIX.length), 10);
                const target = citations.get(n);
                // Unresolved marker (no matching Sources entry) — render as inert text, not a link.
                return target ? <CitationMarker n={n} target={target} /> : <>[{n}]</>;
            }

            // Web links (http/https): render as sleek compact pill with hover preview card
            if (href.startsWith('http://') || href.startsWith('https://')) {
                const matchingSource = sources?.find(s => s.url === href);
                return (
                    <InlineLinkPill
                        href={href}
                        title={matchingSource?.title}
                    >
                        {props.children}
                    </InlineLinkPill>
                );
            }

            // Handle local file links via pywebview api
            if (href.startsWith('file://')) {
                return (
                    <a
                        {...props}
                        href="#"
                        className="markdown-link"
                        onClick={(e) => {
                            e.preventDefault();
                            if (window.pywebview && window.pywebview.api) {
                                window.pywebview.api.download_local_file(href);
                            }
                        }}
                    />
                );
            }

            // Handle data: URIs for self-contained downloads
            if (href.startsWith('data:')) {
                return (
                    <a 
                        {...props} 
                        download={typeof props.children === 'string' ? props.children : 'document'}
                        className="markdown-link" 
                    />
                );
            }
            
            // Default behavior for other links
            return (
                <a 
                    {...props} 
                    href="#" 
                    className="markdown-link" 
                    onClick={(e) => {
                        e.preventDefault();
                        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external_url) {
                            window.pywebview.api.open_external_url(href);
                        } else {
                            // Fallback for browser testing or if API not available yet
                            window.open(href, '_blank', 'noopener,noreferrer');
                        }
                    }}
                >
                    {props.children}
                </a>
            );
        },
    }), [citations, sources]);

    if (!content) return null;

    const youtubeRegex = /<youtube\s+id="([^"]+)"\s+title="([^"]+)"\s*\/>/g;
    const parts: Array<{ type: 'text' | 'youtube'; value?: string; id?: string; title?: string }> = [];
    let lastIndex = 0;
    let match;

    youtubeRegex.lastIndex = 0;

    while ((match = youtubeRegex.exec(content)) !== null) {
        const textSegment = content.substring(lastIndex, match.index);
        if (textSegment) {
            parts.push({ type: 'text', value: textSegment });
        }
        parts.push({ type: 'youtube', id: match[1], title: match[2] });
        lastIndex = youtubeRegex.lastIndex;
    }

    const textSegment = content.substring(lastIndex);
    if (textSegment) {
        parts.push({ type: 'text', value: textSegment });
    }

    return (
        <div className="markdown-content">
            {parts.map((part, index) => {
                if (part.type === 'youtube') {
                    return (
                        <YouTubePlayerCard
                            key={`yt-${index}`}
                            id={part.id!}
                            title={part.title!}
                        />
                    );
                }
                return (
                    <ReactMarkdown
                        key={`md-${index}`}
                        remarkPlugins={[remarkGfm]}
                        components={components}
                        // react-markdown's default sanitizer strips unrecognized URL schemes
                        // (would otherwise turn our internal "cite:N" citation links into "").
                        urlTransform={(url) => (url.startsWith(CITATION_HREF_PREFIX) ? url : defaultUrlTransform(url))}
                    >
                        {part.value!}
                    </ReactMarkdown>
                );
            })}
        </div>
    );
};

// Memoized: during a streaming turn the store updates ~28×/s, re-rendering
// ChatStream. Without this every committed history bubble would re-parse its full
// markdown on each batch ("the entire conversation history is re-rendered with each
// new token"). With a stable `content` prop, memo skips them; only the live bubble,
// whose content actually grows, re-parses.
export default React.memo(MarkdownMessage);
