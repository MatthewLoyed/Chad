// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import SourcesFooter from './SourcesFooter';
import type { SourceEntry } from '../../utils/sources';

afterEach(cleanup);

function makeSource(overrides: Partial<SourceEntry>): SourceEntry {
    return {
        url: 'https://example.com/',
        title: 'Example Vendor',
        domain: 'example.com',
        success: true,
        ...overrides,
    };
}

describe('SourcesFooter media strip (issue #334)', () => {
    it('renders nothing when there are no sources', () => {
        const { container } = render(<SourcesFooter sources={[]} />);
        expect(container.firstChild).toBeNull();
    });

    it('renders no media strip when no source has media', () => {
        const { container } = render(<SourcesFooter sources={[makeSource({})]} />);
        fireEvent.click(screen.getByText(/Sources/i));
        expect(container.querySelector('.web-search-media')).toBeNull();
    });

    it('renders image + video chip once expanded when sources carry media', () => {
        const sources = [
            makeSource({
                url: 'https://vendor-a.com/',
                media: {
                    images: [{ url: 'https://vendor-a.com/hero.jpg', alt: 'Hero', role: 'og:image' }],
                    videos: [{ url: 'https://youtube.com/watch?v=abc', title: 'Venue Tour', platform: 'youtube' }],
                },
            }),
        ];
        render(<SourcesFooter sources={sources} />);
        fireEvent.click(screen.getByText(/Sources/i));

        const img = screen.getByAltText('Hero') as HTMLImageElement;
        expect(img.src).toBe('https://vendor-a.com/hero.jpg');
        expect(screen.getByText('Venue Tour')).toBeTruthy();
    });
});
