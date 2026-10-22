/**
 * Tier-1 frame recorder: tee the live Brain→Limb seam to a JSONL fixture.
 *
 * Run ONE real session in the web UI, then download the captured frames and drop
 * the file into ./fixtures. replayTurn.ts can then replay it deterministically
 * forever — no Brain, no LLM, no hardware required.
 *
 * Usage (paste into the browser devtools console while the web UI is running, or
 * call installFrameRecorder() early in main.tsx behind an env flag):
 *
 *     import { installFrameRecorder, downloadRecordedFrames } from './testkit/frameRecorder';
 *     installFrameRecorder();
 *     // ... send a prompt, let the turn finish ...
 *     downloadRecordedFrames('turn_my_scenario.jsonl');
 */
import type { RecordedFrame } from './wireFrames';

declare global {
    interface Window {
        __recordedFrames?: RecordedFrame[];
        /** Start capturing (dev builds install this automatically). */
        chadRecordFrames?: () => void;
        /** Download what's been captured so far as a JSONL fixture. */
        chadDownloadFrames?: (filename?: string) => void;
        /** How many frames are buffered — handy to confirm capture is live. */
        chadFrameCount?: () => number;
    }
}

/**
 * Wrap the three window.handle* seam functions so every frame is appended to
 * window.__recordedFrames before the real handler runs. Idempotent.
 */
export function installFrameRecorder(): void {
    if (window.__recordedFrames) return; // already installed
    const buf: RecordedFrame[] = (window.__recordedFrames = []);

    const wrapThinking = window.handleThinkingEvent?.bind(window);
    window.handleThinkingEvent = (event) => {
        buf.push({ ch: 'thinking', frame: event as unknown as Record<string, unknown> });
        wrapThinking?.(event);
    };

    const wrapToken = window.handleTokenUsage?.bind(window);
    window.handleTokenUsage = (usage) => {
        buf.push({ ch: 'token', frame: usage as unknown as Record<string, unknown> });
        wrapToken?.(usage);
    };

    const wrapCaption = window.handleCaptionUpdate?.bind(window);
    window.handleCaptionUpdate = (text, append = false) => {
        buf.push({ ch: 'caption', text, append });
        wrapCaption?.(text, append);
    };

    const wrapState = window.handleStateUpdate?.bind(window);
    window.handleStateUpdate = (state) => {
        buf.push({ ch: 'state', state });
        wrapState?.(state);
    };

    // The typed seam — this is what actually drives rendering. main_window.py pushes
    // BOTH this and the legacy `thinking` channel, so capturing only the latter gave
    // fixtures that had to be reconstructed through legacyFrameBridge rather than
    // replayed as-is. Recording it verbatim is what closes the gap between a fixture
    // and what the user really saw.
    const wrapStream = window.applyStreamEvent?.bind(window);
    window.applyStreamEvent = (events) => {
        buf.push({ ch: 'stream', events: events as unknown[] });
        wrapStream?.(events);
    };

    // eslint-disable-next-line no-console
    console.info(
        '[frameRecorder] installed — frames accumulating in window.__recordedFrames. '
        + 'Finish the turn, then call window.chadDownloadFrames("turn_x.jsonl").',
    );
}

/**
 * Expose the recorder on `window` so capturing a scenario is two clicks in devtools
 * rather than remembering a dynamic-import incantation. Called automatically in dev
 * builds from main.tsx; harmless to call twice.
 */
export function exposeFrameRecorder(): void {
    window.chadRecordFrames = () => installFrameRecorder();
    window.chadDownloadFrames = (filename?: string) => downloadRecordedFrames(filename);
    window.chadFrameCount = () => (window.__recordedFrames ?? []).length;
}

/** Serialize captured frames to a JSONL string (one frame per line). */
export function recordedFramesToJsonl(): string {
    return (window.__recordedFrames ?? []).map((f) => JSON.stringify(f)).join('\n');
}

/** Trigger a browser download of the captured frames as a JSONL fixture. */
export function downloadRecordedFrames(filename = 'recorded_turn.jsonl'): void {
    const blob = new Blob([recordedFramesToJsonl()], { type: 'application/x-ndjson' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}
