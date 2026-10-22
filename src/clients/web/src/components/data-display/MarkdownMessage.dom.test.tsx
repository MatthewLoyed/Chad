// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import MarkdownMessage from './MarkdownMessage';
import type { SourceEntry } from '../../utils/sources';

afterEach(cleanup);

describe('MarkdownMessage inline citations (issue #530)', () => {
    it('renders plain content untouched when there is no Sources section', () => {
        render(<MarkdownMessage content="Riu Palace is a nice resort." />);
        expect(screen.getByText('Riu Palace is a nice resort.')).toBeTruthy();
    });

    it('strips the ## Sources block and renders [N] as a clickable citation marker', () => {
        const content =
            'Riu Palace runs $1,233–$2,353/pp [1].\n\n' +
            '## Sources\n' +
            '[1] [honeymoonsinc.com](https://honeymoonsinc.com/riu-palace)\n';
        render(<MarkdownMessage content={content} />);

        // The raw "## Sources" heading text should not be rendered as prose.
        expect(screen.queryByText(/^Sources$/)).toBeNull();

        const marker = screen.getByRole('button', { name: '1' });
        expect(marker).toBeTruthy();
        expect(marker.closest('.citation-marker')?.textContent).toContain('honeymoonsinc.com');
    });

    it('opens the source URL when a citation marker is clicked', () => {
        const content = 'Fact [1].\n\n## Sources\n[1] [site.com](https://site.com/page)\n';
        const originalOpen = window.open;
        let openedUrl: string | undefined;
        window.open = ((url?: string | URL) => {
            openedUrl = String(url);
            return null;
        }) as typeof window.open;

        try {
            render(<MarkdownMessage content={content} />);
            fireEvent.click(screen.getByRole('button', { name: '1' }));
            expect(openedUrl).toBe('https://site.com/page');
        } finally {
            window.open = originalOpen;
        }
    });

    it('prefers title/domain from known sources for the marker tooltip', () => {
        const content = 'Fact [1].\n\n## Sources\n[1] [raw-text](https://site.com/page)\n';
        const sources: SourceEntry[] = [
            { url: 'https://site.com/page', title: 'Curated Title', domain: 'site.com' },
        ];
        render(<MarkdownMessage content={content} sources={sources} />);
        expect(screen.getByText('Curated Title')).toBeTruthy();
    });

    it('leaves an unresolved [N] marker as inert text', () => {
        const content = 'Some text mentioning [1] with no Sources section.';
        render(<MarkdownMessage content={content} />);
        expect(screen.queryByRole('button', { name: '1' })).toBeNull();
        expect(screen.getByText(/Some text mentioning \[1\] with no Sources section\./)).toBeTruthy();
    });
});
