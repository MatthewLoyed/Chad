import type { ThinkingEvent } from '../store';

const YOUTUBE_ID_RE = /^[0-9A-Za-z_-]{11}$/;

function extractYouTubeId(value: unknown): string | null {
    if (typeof value !== 'string' || !value.trim()) return null;
    const raw = value.trim();
    if (YOUTUBE_ID_RE.test(raw)) return raw;
    const lower = raw.toLowerCase();
    if (!lower.includes('youtube.com') && !lower.includes('youtu.be')) return null;
    const patterns = [
        /(?:v=|\/)([0-9A-Za-z_-]{11})/,
        /youtu\.be\/([0-9A-Za-z_-]{11})/,
        /embed\/([0-9A-Za-z_-]{11})/,
    ];
    for (const pattern of patterns) {
        const match = raw.match(pattern);
        if (match?.[1]) return match[1];
    }
    return null;
}

/** Resolved YouTube inline player payload from a show_youtube_video tool event. */
export function resolveYouTubeDisplay(event: ThinkingEvent): { id: string; title: string } | null {
    if (event.name !== 'show_youtube_video') return null;

    const params = event.params ?? {};
    const id =
        event.youtube_video_id
        ?? extractYouTubeId(params.video_id as string | undefined)
        ?? extractYouTubeId(params.url as string | undefined);
    const title = (event.youtube_title ?? params.title) as string | undefined;

    if (!id || !title || !String(title).trim()) return null;
    if (event.success !== true) return null;

    return { id, title: String(title).trim() };
}

export function isYouTubeDisplayTool(event: ThinkingEvent): boolean {
    return event.name === 'show_youtube_video';
}
