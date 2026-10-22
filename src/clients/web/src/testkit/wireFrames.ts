/**
 * Wire-frame types for the golden-frame replay harness.
 *
 * These mirror EXACTLY what the Python bridge pushes across the WebView/WS seam:
 *   - event_bridge.py  → main_window.push_thinking_event → window.handleThinkingEvent(frame)
 *   - event_bridge.py  → main_window.push_token_usage    → window.handleTokenUsage(frame)
 *   - event_bridge.py  → on_caption_update               → window.handleCaptionUpdate(text, append)
 *
 * A recorded session is a JSONL file: one RecordedFrame per line, in arrival order.
 * Capture real frames with frameRecorder.ts, then replay them with replayTurn.ts.
 */

/** A thinking/plan/tool/run event — the payload handed to window.handleThinkingEvent. */
export interface ThinkingFrame {
    ch: 'thinking';
    frame: Record<string, unknown>;
}

/** A token-usage telemetry frame — the payload handed to window.handleTokenUsage. */
export interface TokenFrame {
    ch: 'token';
    frame: Record<string, unknown>;
}

/** A streamed speech caption — args handed to window.handleCaptionUpdate(text, append). */
export interface CaptionFrame {
    ch: 'caption';
    text: string;
    append: boolean;
}

/**
 * A turn-lifecycle state transition — the arg handed to window.handleStateUpdate.
 * Crucial for replay fidelity: the 'ready'/'speaking' transition is what flushes
 * the live thinking buffer and tears down the active-turn block. Without it a
 * replay can never reproduce the flush/handover boundary where most streaming
 * bugs occur.
 */
export interface StateFrame {
    ch: 'state';
    state: string;
}

/**
 * A batch of TYPED stream events — the arg handed to window.applyStreamEvent.
 *
 * This is the seam that actually drives rendering since the streaming refactor
 * (src/remote/stream_events.py → streamReducer → streamTurn). The legacy
 * `thinking` channel is still emitted by main_window.py in parallel, so a recording
 * that only captured that was a lossy mirror of what the user really saw: it had to
 * be reconstructed through legacyFrameBridge instead of replayed as-is.
 *
 * When a fixture contains `stream` frames they are replayed verbatim and the bridged
 * legacy frames are ignored, so the replay drives the reducer with exactly the events
 * production did.
 */
export interface StreamFrame {
    ch: 'stream';
    events: unknown[];
}

export type RecordedFrame = ThinkingFrame | TokenFrame | CaptionFrame | StateFrame | StreamFrame;

/** True when a recording carries real typed stream events (preferred over bridging). */
export function hasTypedStreamFrames(frames: RecordedFrame[]): boolean {
    return frames.some((f) => f.ch === 'stream');
}

/** Parse a JSONL fixture body into ordered frames (blank lines ignored). */
export function parseFramesJsonl(body: string): RecordedFrame[] {
    return body
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0)
        .map((line) => JSON.parse(line) as RecordedFrame);
}
