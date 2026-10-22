import type { StreamEvent } from '../../protocol/streamEvents';
import { getFileTypeInfo } from './artifactTypes';
import { openArtifactPreview } from './openArtifactPreview';

let lastPreviewedToken: string | null = null;

/** Clear dedup state at the start of each turn. */
export function resetAutoPreviewDedup(): void {
    lastPreviewedToken = null;
}

/**
 * Open the right-hand artifact panel when a verified HTML artifact completes.
 * Called after stream events are applied (side effect — not in the reducer).
 */
export function maybeAutoPreviewFromStreamEvents(events: StreamEvent[]): void {
    for (const event of events) {
        if (event.type === 'run_started') {
            resetAutoPreviewDedup();
            continue;
        }
        if (event.type !== 'tool_end') {
            continue;
        }
        if (!event.ok || !event.auto_preview || !event.download_token || !event.filename) {
            continue;
        }
        const info = getFileTypeInfo(event.filename);
        if (!info.canPreview) {
            continue;
        }
        // Images render directly inline in chat — do not auto-pop side panel or modal
        const ext = event.filename.split('.').pop()?.toLowerCase() ?? '';
        if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) {
            continue;
        }
        if (event.download_token === lastPreviewedToken) {
            continue;
        }
        lastPreviewedToken = event.download_token;
        openArtifactPreview({ token: event.download_token, filename: event.filename });
    }
}
