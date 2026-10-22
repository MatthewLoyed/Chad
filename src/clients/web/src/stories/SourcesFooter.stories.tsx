import type { Meta, StoryObj } from '@storybook/react-vite';
import SourcesFooter from '../components/data-display/SourcesFooter';
import type { SourceEntry } from '../utils/sources';

const meta = {
    title: 'Data Display/SourcesFooter',
    component: SourcesFooter,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'Expandable "Sources" control shown at the bottom of an assistant turn that ' +
                    'used web_search — deduped URLs across all search calls in the turn (issue #333). ' +
                    'When sources carry a `media` field (og:image/JSON-LD video, issue #331), expanding ' +
                    'also reveals a deduped image strip + video chips (issue #334).',
            },
        },
    },
} satisfies Meta<typeof SourcesFooter>;

export default meta;
type Story = StoryObj<typeof meta>;

const makeSource = (overrides: Partial<SourceEntry>): SourceEntry => ({
    url: 'https://example.com/',
    title: 'Example Vendor',
    domain: 'example.com',
    contentSource: 'playwright:trafilatura_precision',
    success: true,
    ...overrides,
});

export const Empty: Story = {
    name: 'Empty (renders nothing)',
    args: { sources: [] },
};

export const SingleSource: Story = {
    name: 'One Source',
    args: {
        sources: [makeSource({})],
    },
};

export const ManySources: Story = {
    name: 'Many Sources',
    args: {
        sources: [
            makeSource({ url: 'https://vendor-a.com/', title: 'Vendor A — Wedding Venues', domain: 'vendor-a.com' }),
            makeSource({ url: 'https://vendor-b.com/', title: 'Vendor B Photography', domain: 'vendor-b.com', contentSource: 'trafilatura' }),
            makeSource({
                url: 'https://vendor-c.com/blocked',
                title: 'Vendor C (fetch failed)',
                domain: 'vendor-c.com',
                success: false,
                contentSource: null,
            }),
            makeSource({ url: 'https://vendor-d.com/', title: 'Vendor D Catering', domain: 'vendor-d.com' }),
            makeSource({ url: 'https://vendor-e.com/', title: 'Vendor E Florist', domain: 'vendor-e.com' }),
        ],
    },
};

// --- Rich media variants (issue #334, expanded by default via play/initial state N/A —
// use the Storybook control panel's "expanded" toggle isn't wired; these default to expanded
// visually by having a single obvious source so reviewers click "Sources (N)" to reveal media). ---

export const OneImage: Story = {
    name: 'Media: One Image',
    args: {
        sources: [
            makeSource({
                url: 'https://vendor-a.com/',
                title: 'Vendor A — Wedding Venues',
                domain: 'vendor-a.com',
                media: { images: [{ url: 'https://picsum.photos/seed/vendor-a/300/300', alt: 'Venue hero', role: 'og:image' }], videos: [] },
            }),
        ],
    },
};

export const ImageGallery: Story = {
    name: 'Media: Image Gallery',
    args: {
        sources: [
            makeSource({
                url: 'https://vendor-a.com/',
                title: 'Vendor A — Wedding Venues',
                domain: 'vendor-a.com',
                media: { images: [{ url: 'https://picsum.photos/seed/a/300/300', alt: 'Venue A', role: 'og:image' }], videos: [] },
            }),
            makeSource({
                url: 'https://vendor-b.com/',
                title: 'Vendor B Photography',
                domain: 'vendor-b.com',
                media: { images: [{ url: 'https://picsum.photos/seed/b/300/300', alt: 'Venue B', role: 'og:image' }], videos: [] },
            }),
            makeSource({
                url: 'https://vendor-c.com/',
                title: 'Vendor C Catering',
                domain: 'vendor-c.com',
                media: { images: [{ url: 'https://picsum.photos/seed/c/300/300', alt: 'Venue C', role: 'dom:hero' }], videos: [] },
            }),
        ],
    },
};

export const VideoChip: Story = {
    name: 'Media: Video Chip',
    args: {
        sources: [
            makeSource({
                url: 'https://youtube.com/watch?v=abc12345678',
                title: 'Vendor A — Venue Tour',
                domain: 'youtube.com',
                media: { images: [], videos: [{ url: 'https://youtube.com/watch?v=abc12345678', title: 'Venue Tour Walkthrough', platform: 'youtube' }] },
            }),
        ],
    },
};

export const MixedMedia: Story = {
    name: 'Media: Mixed (images + video)',
    args: {
        sources: [
            makeSource({
                url: 'https://vendor-a.com/',
                title: 'Vendor A — Wedding Venues',
                domain: 'vendor-a.com',
                media: {
                    images: [{ url: 'https://picsum.photos/seed/mixed-a/300/300', alt: 'Venue A', role: 'og:image' }],
                    videos: [{ url: 'https://youtube.com/watch?v=mixed1', title: 'Venue A Tour', platform: 'youtube' }],
                },
            }),
            makeSource({
                url: 'https://vendor-b.com/',
                title: 'Vendor B Photography',
                domain: 'vendor-b.com',
                media: {
                    images: [{ url: 'https://picsum.photos/seed/mixed-b/300/300', alt: 'Venue B', role: 'dom:hero' }],
                    videos: [],
                },
            }),
            makeSource({
                url: 'https://vimeo.com/76979871',
                title: 'Vendor C Highlight Reel',
                domain: 'vimeo.com',
                media: { images: [], videos: [{ url: 'https://vimeo.com/76979871', title: 'Vendor C Highlight Reel', platform: 'vimeo' }] },
            }),
        ],
    },
};
