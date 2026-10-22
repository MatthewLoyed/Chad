import { describe, expect, it } from 'vitest';
import { collectTurnMedia, extractInlineCitations, extractSourcesFromThoughts } from './sources';
import type { ThinkingEvent } from '../store';
import type { SourceEntry } from './sources';

function webSearchEvent(resultObj: unknown, overrides: Partial<ThinkingEvent> = {}): ThinkingEvent {
    return {
        type: 'tool_start',
        name: 'web_search',
        success: true,
        result: JSON.stringify(resultObj),
        ...overrides,
    };
}

const onePageResult = {
    query_count: 1,
    results: [
        {
            query: 'wedding venues',
            sources: [
                { url: 'https://vendor-a.com/', title: 'Vendor A', content: '...', content_source: 'trafilatura', success: true },
            ],
            source_count: 1,
        },
    ],
};

describe('extractSourcesFromThoughts', () => {
    it('returns an empty array for no thoughts', () => {
        expect(extractSourcesFromThoughts(undefined)).toEqual([]);
        expect(extractSourcesFromThoughts([])).toEqual([]);
    });

    it('extracts sources from a single web_search call', () => {
        const sources = extractSourcesFromThoughts([webSearchEvent(onePageResult)]);
        expect(sources).toEqual([
            { url: 'https://vendor-a.com/', title: 'Vendor A', domain: 'vendor-a.com', contentSource: 'trafilatura', success: true },
        ]);
    });

    it('flattens multiple queries and dedupes repeated URLs across calls', () => {
        const multiQuery = {
            results: [
                { query: 'q1', sources: [{ url: 'https://a.com/', title: 'A', success: true }] },
                { query: 'q2', sources: [{ url: 'https://a.com/', title: 'A dup', success: true }, { url: 'https://b.com/', title: 'B', success: true }] },
            ],
        };
        const sources = extractSourcesFromThoughts([
            webSearchEvent(multiQuery),
            webSearchEvent({ results: [{ query: 'q3', sources: [{ url: 'https://b.com/', title: 'B dup', success: true }] }] }),
        ]);
        expect(sources.map((s) => s.url)).toEqual(['https://a.com/', 'https://b.com/']);
    });

    it('walks nested subagent/delegation tool calls (children)', () => {
        const parent: ThinkingEvent = {
            type: 'subagent_group',
            agent_name: 'research_agent',
            children: [webSearchEvent(onePageResult)],
        };
        const sources = extractSourcesFromThoughts([parent]);
        expect(sources).toHaveLength(1);
        expect(sources[0].url).toBe('https://vendor-a.com/');
    });

    it('skips a truncated/corrupted result string instead of throwing', () => {
        const truncated = webSearchEvent(onePageResult);
        truncated.result = (truncated.result as string).substring(0, 30) + '... (result truncated for memory optimization)';
        expect(() => extractSourcesFromThoughts([truncated])).not.toThrow();
        expect(extractSourcesFromThoughts([truncated])).toEqual([]);
    });

    it('ignores tool calls that are not web_search or did not succeed', () => {
        const other = webSearchEvent(onePageResult, { name: 'write_file' });
        const failed = webSearchEvent(onePageResult, { success: false });
        expect(extractSourcesFromThoughts([other, failed])).toEqual([]);
    });

    it('falls back to the domain as title when a hit has no title', () => {
        const noTitle = { results: [{ query: 'q', sources: [{ url: 'https://vendor-x.com/', success: true }] }] };
        const sources = extractSourcesFromThoughts([webSearchEvent(noTitle)]);
        expect(sources[0].title).toBe('vendor-x.com');
    });

    it('parses a hit media field into images/videos', () => {
        const withMedia = {
            results: [
                {
                    query: 'q',
                    sources: [
                        {
                            url: 'https://vendor-a.com/',
                            title: 'Vendor A',
                            success: true,
                            media: {
                                images: [{ url: 'https://vendor-a.com/hero.jpg', alt: 'Hero', role: 'og:image' }],
                                videos: [{ url: 'https://youtube.com/watch?v=abc', title: 'Tour', platform: 'youtube' }],
                            },
                        },
                    ],
                },
            ],
        };
        const sources = extractSourcesFromThoughts([webSearchEvent(withMedia)]);
        expect(sources[0].media).toEqual({
            images: [{ url: 'https://vendor-a.com/hero.jpg', alt: 'Hero', role: 'og:image' }],
            videos: [{ url: 'https://youtube.com/watch?v=abc', title: 'Tour', platform: 'youtube' }],
        });
    });

    it('leaves media undefined when a hit has no media field', () => {
        const sources = extractSourcesFromThoughts([webSearchEvent(onePageResult)]);
        expect(sources[0].media).toBeUndefined();
    });
});

describe('collectTurnMedia', () => {
    it('returns null when no source has media', () => {
        const sources = extractSourcesFromThoughts([webSearchEvent(onePageResult)]);
        expect(collectTurnMedia(sources)).toBeNull();
    });

    it('aggregates and dedupes media across sources', () => {
        const withMedia = {
            results: [
                {
                    query: 'q',
                    sources: [
                        {
                            url: 'https://a.com/',
                            success: true,
                            media: {
                                images: [{ url: 'https://a.com/hero.jpg' }],
                                videos: [{ url: 'https://youtube.com/watch?v=1', title: 'One', platform: 'youtube' }],
                            },
                        },
                        {
                            url: 'https://b.com/',
                            success: true,
                            media: {
                                images: [{ url: 'https://a.com/hero.jpg' }, { url: 'https://b.com/thumb.jpg' }],
                                videos: [{ url: 'https://youtube.com/watch?v=1', title: 'One dup', platform: 'youtube' }],
                            },
                        },
                    ],
                },
            ],
        };
        const sources = extractSourcesFromThoughts([webSearchEvent(withMedia)]);
        const media = collectTurnMedia(sources);
        expect(media?.images.map((i) => i.url)).toEqual(['https://a.com/hero.jpg', 'https://b.com/thumb.jpg']);
        expect(media?.videos).toEqual([{ url: 'https://youtube.com/watch?v=1', title: 'One', platform: 'youtube' }]);
    });
});

describe('extractInlineCitations', () => {
    it('returns the content unchanged and no citations when there is no Sources section', () => {
        const content = 'Riu Palace runs $1,233 per person.';
        expect(extractInlineCitations(content)).toEqual({ content, citations: new Map() });
    });

    it('parses a trailing ## Sources block and strips it from the content', () => {
        const content =
            'Riu Palace runs $1,233–$2,353/pp [1].\n\n' +
            '## Sources\n' +
            '[1] [honeymoonsinc.com](https://honeymoonsinc.com/riu-palace)\n';
        const { content: cleaned, citations } = extractInlineCitations(content);
        expect(cleaned).toBe('Riu Palace runs $1,233–$2,353/pp [1].');
        expect(citations.get(1)).toEqual({
            url: 'https://honeymoonsinc.com/riu-palace',
            title: 'honeymoonsinc.com',
            domain: 'honeymoonsinc.com',
        });
    });

    it('prefers title/domain from known sources over the parsed Sources line text', () => {
        const content =
            'Price is $1,233 [1].\n\n## Sources\n[1] [raw-link-text](https://honeymoonsinc.com/riu-palace)\n';
        const known: SourceEntry[] = [
            { url: 'https://honeymoonsinc.com/riu-palace', title: 'Riu Palace — Honeymoons Inc', domain: 'honeymoonsinc.com' },
        ];
        const { citations } = extractInlineCitations(content, known);
        expect(citations.get(1)?.title).toBe('Riu Palace — Honeymoons Inc');
    });

    it('parses multiple numbered sources', () => {
        const content =
            'Fact A [1]. Fact B [2].\n\n## Sources\n' +
            '[1] [site-a.com](https://site-a.com/page)\n' +
            '[2] [site-b.com](https://site-b.com/page)\n';
        const { citations } = extractInlineCitations(content);
        expect(citations.size).toBe(2);
        expect(citations.get(2)?.url).toBe('https://site-b.com/page');
    });
});
