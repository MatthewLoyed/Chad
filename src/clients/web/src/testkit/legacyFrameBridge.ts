/**
 * Phase 1 step 4: legacy RecordedFrame → typed StreamEvent bridge.
 * ===============================================================
 *
 * Converts the CURRENT wire frames (what main_window.py pushes today — captured
 * as RecordedFrames in the Phase 0 fixtures) into the target StreamEvent union.
 * This lets every existing golden fixture drive the new `applyStreamEvent`
 * reducer, so we can prove the reducer reproduces the legacy seam's output
 * (thinking text, answer text, tool cards, plan) BEFORE wiring the typed protocol
 * into production — see docs/features/STREAMING_REFACTOR.md, Phase 1.
 *
 * The mapping mirrors the reference table at the bottom of protocol/streamEvents.ts.
 * Once the Python emitter (step 2) emits StreamEvents natively this bridge is only
 * needed to keep the legacy fixtures alive; new fixtures record StreamEvents directly.
 */
import type { DistributiveOmit, StreamEvent, ToolEnd } from '../protocol/streamEvents';
import type { PlanStep } from '../store';
import type { RecordedFrame } from './wireFrames';

/** Reasoning frame types that map to a reasoning_delta. */
const REASONING_TYPES = new Set(['reasoning', 'thought', 'thinking']);

interface BridgeCursor {
    turnId: string;
    seq: number;
    /** Id of the currently-open reasoning part, or null between reasoning runs. */
    reasoningPartId: string | null;
    reasoningCount: number;
    /** Id of the currently-open text part, or null before/after a finalize. */
    textPartId: string | null;
    textCount: number;
}

/**
 * Convert one recorded session into an ordered StreamEvent stream.
 *
 * Part-boundary rules (match the legacy block grouping so equivalence holds):
 *  - Consecutive reasoning frames coalesce into ONE reasoning part; any non-reasoning
 *    event (tool/speech/plan) closes it, and the next reasoning frame opens a new part.
 *  - Caption deltas accumulate into the current text part; a `speech` (append:false)
 *    event finalizes that part (text_end) and the next caption opens a new one.
 *  - run_finished closes any still-open text part.
 */
export function recordedFramesToStreamEvents(frames: RecordedFrame[]): StreamEvent[] {
    const out: StreamEvent[] = [];
    const cur: BridgeCursor = {
        turnId: 'turn_legacy',
        seq: 0,
        reasoningPartId: null,
        reasoningCount: 0,
        textPartId: null,
        textCount: 0,
    };

    const emit = (e: DistributiveOmit<StreamEvent, 'turn_id' | 'seq'>): void => {
        out.push({ ...e, turn_id: cur.turnId, seq: cur.seq++ } as StreamEvent);
    };

    // Close the open reasoning part (if any) when a non-reasoning event arrives.
    const closeReasoning = (): void => {
        if (cur.reasoningPartId !== null) {
            emit({ type: 'reasoning_end', part_id: cur.reasoningPartId });
            cur.reasoningPartId = null;
        }
    };

    for (const frame of frames) {
        if (frame.ch === 'token') {
            emit({ type: 'token_usage', usage: frame.frame as never });
            continue;
        }

        if (frame.ch === 'caption') {
            // Only the append stream carries answer deltas; the non-append caption
            // path in the legacy seam just (re)sets streamingResponse and is
            // represented here by the speech finalize below.
            if (frame.append) {
                if (cur.textPartId === null) cur.textPartId = `text-${cur.textCount++}`;
                emit({ type: 'text_delta', part_id: cur.textPartId, delta: frame.text });
            }
            continue;
        }

        if (frame.ch === 'state') {
            // Lifecycle/status is derived by the Phase 2 state machine; no event.
            continue;
        }

        if (frame.ch === 'stream') {
            // Already-typed events from a real capture. This bridge exists to
            // RECONSTRUCT events from the legacy channel; when a recording has the real
            // ones, domReplay replays them directly instead of calling in here.
            continue;
        }

        // frame.ch === 'thinking'
        const f = frame.frame as {
            type?: string;
            text?: string;
            run_id?: string;
            correlation_id?: string;
            name?: string;
            params?: Record<string, unknown>;
            display_text?: string;
            success?: boolean;
            result?: unknown;
            response?: string;
            agent_name?: string;
            parent_id?: string;
            activity_type?: string;
            content?: {
                steps?: PlanStep[];
                goal?: string;
                tools?: string[];
                shortlist_size?: number;
                selection_path?: string;
            };
            download_token?: string;
            filename?: string;
            youtube_video_id?: string;
            youtube_title?: string;
            auto_preview?: boolean;
            awaiting_user?: boolean;
            map_view?: Record<string, unknown>;
        };

        switch (f.type) {
            case 'run_started':
                cur.turnId = f.run_id || cur.turnId;
                emit({ type: 'run_started' });
                break;

            case 'reasoning':
            case 'thought':
            case 'thinking':
                if (REASONING_TYPES.has(f.type)) {
                    if (cur.reasoningPartId === null) cur.reasoningPartId = `reasoning-${cur.reasoningCount++}`;
                    emit({
                        type: 'reasoning_delta',
                        part_id: cur.reasoningPartId,
                        delta: f.text || '',
                        agent_name: f.agent_name,
                    });
                }
                break;

            case 'tool_start':
                closeReasoning();
                emit({
                    type: 'tool_start',
                    tool_call_id: f.correlation_id || `tool-${cur.seq}`,
                    name: f.name || 'unknown',
                    args: f.params,
                    display_text: f.display_text,
                    parent_id: f.parent_id,
                });
                break;

            case 'tool_end':
                closeReasoning();
                emit({
                    type: 'tool_end',
                    tool_call_id: f.correlation_id || `tool-${cur.seq}`,
                    ok: f.success === true,
                    result: f.result,
                    download_token: f.download_token,
                    filename: f.filename,
                    youtube_video_id: f.youtube_video_id,
                    youtube_title: f.youtube_title,
                    auto_preview: f.auto_preview,
                    awaiting_user: f.awaiting_user,
                    map_view: f.map_view as ToolEnd['map_view'],
                });
                break;

            case 'speech':
                // A completed answer block: captions before it were the deltas; this
                // finalizes the part. If no captions opened a part (full text arrived
                // only here), open one with the speech text as its single delta.
                closeReasoning();
                if (cur.textPartId === null) {
                    cur.textPartId = `text-${cur.textCount++}`;
                    emit({ type: 'text_delta', part_id: cur.textPartId, delta: f.text || '' });
                }
                emit({ type: 'text_end', part_id: cur.textPartId });
                cur.textPartId = null;
                break;

            case 'activity_snapshot':
                // PLAN activity is replace-semantics: successive snapshots (each with
                // a fresh message_id in the legacy wire) describe the SAME logical plan
                // and must collapse to one card. Key them by a single per-turn plan id
                // so plan_snapshot's replace overwrites rather than appends.
                if (f.activity_type === 'PLAN' && f.content) {
                    closeReasoning();
                    emit({
                        type: 'plan_snapshot',
                        plan_id: `${cur.turnId}-plan`,
                        goal: f.content.goal,
                        steps: f.content.steps ?? [],
                    });
                } else if (f.activity_type === 'TOOLS' && f.content?.tools) {
                    emit({
                        type: 'tools_snapshot',
                        menu_id: `${cur.turnId}-tools`,
                        tools: f.content.tools,
                        shortlist_size: f.content.shortlist_size,
                        selection_path: f.content.selection_path,
                    });
                }
                break;

            case 'run_finished':
                closeReasoning();
                if (cur.textPartId !== null) {
                    emit({ type: 'text_end', part_id: cur.textPartId });
                    cur.textPartId = null;
                }
                // Some turns stream no captions/speech and deliver the answer ONLY in
                // run_finished.response (legacy: addAIMessageToChat renders it). When no
                // text part streamed, synthesize one from the response so answerText has
                // it. Guard on textCount so caption-streamed turns (whose response merely
                // duplicates the deltas) don't double their answer.
                if (cur.textCount === 0 && f.response) {
                    const id = `text-${cur.textCount++}`;
                    emit({ type: 'text_delta', part_id: id, delta: f.response });
                    emit({ type: 'text_end', part_id: id });
                }
                emit({ type: 'run_finished', response: f.response });
                break;

            default:
                // Unknown legacy thinking type — ignore (Phase 1 covers the wire
                // shapes the fixtures exercise; new shapes get added explicitly).
                break;
        }
    }

    return out;
}
