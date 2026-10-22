/**
 * Derives the deduped "Sources" list for a turn from its `web_search` tool calls.
 *
 * Must run on the RAW (pre-prune, pre-truncate) thought list — `web_search` results are
 * JSON-stringified into `ThinkingEvent.result` and both the live-turn pruning
 * (`pruneEvent` in eventPruning.ts) and history hydration (`App.tsx`) truncate long `result`
 * strings for memory, which would otherwise corrupt the JSON before it can be parsed.
 * Callers extract sources once at finalize/hydration time and store the compact
 * `SourceEntry[]` on the message instead of re-deriving it from (possibly truncated)
 * persisted thoughts later.
 */
import type { ThinkingEvent } from '../store';

export interface SourceMediaImage {
    url: string;
    alt?: string;
    role?: string;
}

export interface SourceMediaVideo {
    url: string;
    title?: string;
    platform?: string;
}

export interface SourceEntry {
    url: string;
    title: string;
    domain: string;
    contentSource?: string | null;
    success?: boolean;
    media?: {
        images: SourceMediaImage[];
        videos: SourceMediaVideo[];
    };
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null;
}

/** Parse a hit's `media` field (see src/tools/web/media_metadata.py, issue #331). */
function parseHitMedia(rawMedia: unknown): SourceEntry['media'] {
    if (!isRecord(rawMedia)) return undefined;

    const images: SourceMediaImage[] = [];
    if (Array.isArray(rawMedia.images)) {
        for (const img of rawMedia.images) {
            if (isRecord(img) && typeof img.url === 'string') {
                images.push({
                    url: img.url,
                    alt: typeof img.alt === 'string' ? img.alt : undefined,
                    role: typeof img.role === 'string' ? img.role : undefined,
                });
            }
        }
    }

    const videos: SourceMediaVideo[] = [];
    if (Array.isArray(rawMedia.videos)) {
        for (const vid of rawMedia.videos) {
            if (isRecord(vid) && typeof vid.url === 'string') {
                videos.push({
                    url: vid.url,
                    title: typeof vid.title === 'string' ? vid.title : undefined,
                    platform: typeof vid.platform === 'string' ? vid.platform : undefined,
                });
            }
        }
    }

    if (images.length === 0 && videos.length === 0) return undefined;
    return { images, videos };
}

export function domainFromUrl(url: string): string {
    try {
        return new URL(url).hostname.replace(/^www\./, '');
    } catch {
        return url;
    }
}

/** Collect every parsed `web_search` result payload in a thought list, including subagent children. */
function collectWebSearchPayloads(thoughts: ThinkingEvent[]): Record<string, unknown>[] {
    const payloads: Record<string, unknown>[] = [];

    const visit = (events: ThinkingEvent[]) => {
        for (const e of events) {
            if (
                (e.type === 'tool_start' || e.type === 'tool_end') &&
                e.name === 'web_search' &&
                e.success &&
                typeof e.result === 'string'
            ) {
                try {
                    const parsed = JSON.parse(e.result);
                    if (parsed && typeof parsed === 'object') {
                        payloads.push(parsed as Record<string, unknown>);
                    }
                } catch {
                    // Truncated or malformed result — skip rather than break the footer.
                }
            }
            if (e.children && e.children.length > 0) {
                visit(e.children);
            }
        }
    };

    visit(thoughts);
    return payloads;
}

/** Flatten + dedupe source URLs from every `web_search` call in a turn (nested/subagent included). */
export function extractSourcesFromThoughts(thoughts?: ThinkingEvent[]): SourceEntry[] {
    if (!thoughts || thoughts.length === 0) return [];

    const seen = new Set<string>();
    const sources: SourceEntry[] = [];

    for (const payload of collectWebSearchPayloads(thoughts)) {
        const queryResults = Array.isArray(payload.results) ? payload.results : [];
        for (const queryResult of queryResults) {
            if (!queryResult || typeof queryResult !== 'object') continue;
            const hits = (queryResult as Record<string, unknown>).sources;
            if (!Array.isArray(hits)) continue;
            for (const hit of hits) {
                if (!hit || typeof hit !== 'object') continue;
                const h = hit as Record<string, unknown>;
                const url = typeof h.url === 'string' ? h.url : null;
                if (!url || seen.has(url)) continue;
                seen.add(url);
                sources.push({
                    url,
                    title: typeof h.title === 'string' && h.title.trim() ? h.title : domainFromUrl(url),
                    domain: domainFromUrl(url),
                    contentSource: typeof h.content_source === 'string' ? h.content_source : null,
                    success: typeof h.success === 'boolean' ? h.success : undefined,
                    media: parseHitMedia(h.media),
                });
            }
        }
    }

    return sources;
}

const MAX_STRIP_IMAGES = 6;
const MAX_STRIP_VIDEOS = 4;

/** Aggregate + dedupe media across a turn's sources for the footer's image strip/video chips. */
export function collectTurnMedia(sources: SourceEntry[]): {
    images: SourceMediaImage[];
    videos: SourceMediaVideo[];
} | null {
    const images: SourceMediaImage[] = [];
    const videos: SourceMediaVideo[] = [];
    const seenImages = new Set<string>();
    const seenVideos = new Set<string>();

    for (const source of sources) {
        if (!source.media) continue;
        for (const img of source.media.images) {
            if (images.length >= MAX_STRIP_IMAGES) break;
            if (seenImages.has(img.url)) continue;
            seenImages.add(img.url);
            images.push(img);
        }
        for (const vid of source.media.videos) {
            if (videos.length >= MAX_STRIP_VIDEOS) break;
            if (seenVideos.has(vid.url)) continue;
            seenVideos.add(vid.url);
            videos.push(vid);
        }
    }

    if (images.length === 0 && videos.length === 0) return null;
    return { images, videos };
}

export interface CitationTarget {
    url: string;
    title: string;
    domain: string;
}

// Matches the trailing `## Sources` block the deep_research_agent prompt requires
// (see _DEEP_RESEARCH_AGENT_FALLBACK_PROMPT in src/core/orchestration/supervisor.py):
// "## Sources\n[1] [title](url)\n[2] [title](url)".
const SOURCES_SECTION_RE = /\n{0,2}#{1,3}\s*Sources\s*\n([\s\S]*)$/i;
const SOURCE_LINE_RE = /\[(\d+)\]\s*\[([^\]]*)\]\(([^)\s]+)\)/g;

/**
 * Pulls the `[N] [title](url)` mapping out of a trailing `## Sources` block and strips that
 * block from the returned content — `SourcesFooter` already renders the canonical source list
 * from tool telemetry, so leaving the raw markdown list in the prose would duplicate it.
 * Known `sources` (from `extractSourcesFromThoughts`) are matched by URL to prefer their
 * title/domain; the parsed line is the fallback when a URL isn't in that list.
 */
export function extractInlineCitations(
    content: string,
    knownSources: SourceEntry[] = [],
): { content: string; citations: Map<number, CitationTarget> } {
    const citations = new Map<number, CitationTarget>();
    const sectionMatch = content.match(SOURCES_SECTION_RE);
    if (!sectionMatch) {
        return { content, citations };
    }

    const sourceByUrl = new Map(knownSources.map((s) => [s.url, s]));
    SOURCE_LINE_RE.lastIndex = 0;
    let lineMatch: RegExpExecArray | null;
    while ((lineMatch = SOURCE_LINE_RE.exec(sectionMatch[1])) !== null) {
        const n = parseInt(lineMatch[1], 10);
        const url = lineMatch[3];
        const known = sourceByUrl.get(url);
        citations.set(n, {
            url,
            title: known?.title || lineMatch[2] || domainFromUrl(url),
            domain: known?.domain || domainFromUrl(url),
        });
    }

    if (citations.size === 0) {
        return { content, citations };
    }

    const cleaned = content.slice(0, sectionMatch.index).replace(/\s+$/, '');
    return { content: cleaned, citations };
}
