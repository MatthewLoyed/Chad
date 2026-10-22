/**
 * Browser stream emitter (#188).
 * ==============================
 *
 * 1:1 TS port of the Python `StreamEventEmitter` (src/remote/stream_events.py),
 * the producing half of the unified stream protocol. The desktop Limb runs the
 * Python emitter and ships its output over the pywebview bridge; a browser-hosted
 * Chad has no Python Limb, so `brainSocket` runs *this* emitter to turn the raw
 * Brain `ChadMessage`s into the same `StreamEvent` union the reducer already
 * consumes (src/clients/web/src/protocol/streamEvents.ts).
 *
 * Part-boundary rules mirror the Python emitter exactly so both languages agree
 * on how a turn decomposes into parts — see stream_events.py for the contract.
 */
import type { StreamEvent, DistributiveOmit } from '../protocol/streamEvents';
import type { PlanStep, TokenUsage } from '../store';

/** A StreamEvent minus the fields the emitter stamps on (`turn_id`, `seq`). */
type EmitInput = DistributiveOmit<StreamEvent, 'turn_id' | 'seq'>;

export class StreamEventEmitter {
    private turnId: string;
    private seq = 0;
    private reasoningPartId: string | null = null;
    private reasoningCount = 0;
    /** Attribution of the open reasoning part — a change closes it so Scout/Planner don't merge. */
    private reasoningAgentName: string | null = null;
    private reasoningParentId: string | null = null;
    private textPartId: string | null = null;
    private textCount = 0;

    constructor(turnId = 'turn') {
        this.turnId = turnId;
    }

    private emit(event: EmitInput): StreamEvent {
        const full = { ...event, turn_id: this.turnId, seq: this.seq } as StreamEvent;
        this.seq += 1;
        return full;
    }

    private closeReasoning(out: StreamEvent[]): void {
        if (this.reasoningPartId !== null) {
            out.push(this.emit({ type: 'reasoning_end', part_id: this.reasoningPartId }));
            this.reasoningPartId = null;
            this.reasoningAgentName = null;
            this.reasoningParentId = null;
        }
    }

    runStarted(turnId?: string): StreamEvent[] {
        if (turnId) this.turnId = turnId;
        this.seq = 0;
        this.reasoningPartId = null;
        this.reasoningCount = 0;
        this.reasoningAgentName = null;
        this.reasoningParentId = null;
        this.textPartId = null;
        this.textCount = 0;
        return [this.emit({ type: 'run_started' })];
    }

    browserProgress(progress: import('../protocol/streamEvents').BrowserProgressPayload): StreamEvent[] {
        const out: StreamEvent[] = [];
        this.closeReasoning(out);
        out.push(this.emit({ type: 'browser_progress', progress }));
        return out;
    }

    runFinished(response?: string, stopReason?: string, turnDurationMs?: number): StreamEvent[] {
        const out: StreamEvent[] = [];
        this.closeReasoning(out);
        if (this.textPartId !== null) {
            out.push(this.emit({ type: 'text_end', part_id: this.textPartId }));
            this.textPartId = null;
        }
        if (this.textCount === 0 && response) {
            const partId = `text-${this.textCount}`;
            this.textCount += 1;
            out.push(this.emit({ type: 'text_delta', part_id: partId, delta: response }));
            out.push(this.emit({ type: 'text_end', part_id: partId }));
        }
        const finished: Record<string, unknown> = { type: 'run_finished' };
        if (response !== undefined) finished.response = response;
        if (stopReason !== undefined) finished.stop_reason = stopReason;
        if (turnDurationMs !== undefined) finished.turn_duration_ms = turnDurationMs;
        out.push(this.emit(finished as EmitInput));
        return out;
    }

    runError(message: string, code?: string): StreamEvent[] {
        const evt: Record<string, unknown> = { type: 'run_error', message };
        if (code !== undefined) evt.code = code;
        return [this.emit(evt as EmitInput)];
    }

    reasoningDelta(text: string, agentName?: string | null, parentId?: string | null): StreamEvent[] {
        if (!text) return [];
        // Parallel research workers execute concurrently; their internal reasoning
        // scratchpad must not fragment or interleave into the user's thought stream.
        if (agentName && agentName.toLowerCase().startsWith('research worker')) {
            return [];
        }
        const nextAgent = agentName ?? null;
        const nextParent = parentId ?? null;
        const out: StreamEvent[] = [];
        // Subagent handoff (e.g. Scout → Planner): do not coalesce across attribution.
        if (
            this.reasoningPartId !== null
            && (nextAgent !== this.reasoningAgentName || nextParent !== this.reasoningParentId)
        ) {
            this.closeReasoning(out);
        }
        if (this.reasoningPartId === null) {
            this.reasoningPartId = `reasoning-${this.reasoningCount}`;
            this.reasoningCount += 1;
            this.reasoningAgentName = nextAgent;
            this.reasoningParentId = nextParent;
        }
        const evt: Record<string, unknown> = {
            type: 'reasoning_delta',
            part_id: this.reasoningPartId,
            delta: text,
        };
        if (nextAgent) evt.agent_name = nextAgent;
        if (nextParent) evt.parent_id = nextParent;
        out.push(this.emit(evt as EmitInput));
        return out;
    }

    textDelta(text: string): StreamEvent[] {
        if (!text) return [];
        if (this.textPartId === null) {
            this.textPartId = `text-${this.textCount}`;
            this.textCount += 1;
        }
        return [this.emit({ type: 'text_delta', part_id: this.textPartId, delta: text })];
    }

    speechFinalize(text?: string): StreamEvent[] {
        const out: StreamEvent[] = [];
        this.closeReasoning(out);
        if (this.textPartId === null) {
            this.textPartId = `text-${this.textCount}`;
            this.textCount += 1;
            out.push(this.emit({ type: 'text_delta', part_id: this.textPartId, delta: text || '' }));
        }
        out.push(this.emit({ type: 'text_end', part_id: this.textPartId }));
        this.textPartId = null;
        return out;
    }

    toolStart(
        toolCallId: string,
        name: string,
        args?: Record<string, unknown> | null,
        displayText?: string | null,
        parentId?: string | null,
    ): StreamEvent[] {
        const out: StreamEvent[] = [];
        this.closeReasoning(out);
        const evt: Record<string, unknown> = { type: 'tool_start', tool_call_id: toolCallId, name };
        if (args !== undefined && args !== null) evt.args = args;
        if (displayText !== undefined && displayText !== null) evt.display_text = displayText;
        if (parentId !== undefined && parentId !== null) evt.parent_id = parentId;
        out.push(this.emit(evt as EmitInput));
        return out;
    }

    toolEnd(
        toolCallId: string,
        ok: boolean,
        result?: unknown,
        downloadToken?: string | null,
        filename?: string | null,
        youtubeVideoId?: string | null,
        youtubeTitle?: string | null,
        autoPreview?: boolean | null,
        awaitingUser?: boolean,
        mapView?: Record<string, unknown> | null,
    ): StreamEvent[] {
        // Close open reasoning so a following agent (Planner after Scout) opens a fresh part.
        const out: StreamEvent[] = [];
        this.closeReasoning(out);
        const evt: Record<string, unknown> = { type: 'tool_end', tool_call_id: toolCallId, ok };
        if (result !== undefined && result !== null) evt.result = result;
        if (downloadToken !== undefined && downloadToken !== null) evt.download_token = downloadToken;
        if (filename !== undefined && filename !== null) evt.filename = filename;
        if (youtubeVideoId !== undefined && youtubeVideoId !== null) evt.youtube_video_id = youtubeVideoId;
        if (youtubeTitle !== undefined && youtubeTitle !== null) evt.youtube_title = youtubeTitle;
        if (autoPreview) evt.auto_preview = true;
        if (awaitingUser) evt.awaiting_user = true;
        if (mapView !== undefined && mapView !== null) evt.map_view = mapView;
        out.push(this.emit(evt as EmitInput));
        return out;
    }

    planSnapshot(steps: PlanStep[], goal?: string | null): StreamEvent[] {
        const out: StreamEvent[] = [];
        this.closeReasoning(out);
        const evt: Record<string, unknown> = {
            type: 'plan_snapshot',
            plan_id: `${this.turnId}-plan`,
            steps,
        };
        if (goal !== undefined && goal !== null) evt.goal = goal;
        out.push(this.emit(evt as EmitInput));
        return out;
    }

    toolsSnapshot(
        tools: string[],
        shortlistSize?: number | null,
        selectionPath?: string | null,
    ): StreamEvent[] {
        const evt: Record<string, unknown> = {
            type: 'tools_snapshot',
            menu_id: `${this.turnId}-tools`,
            tools,
        };
        if (shortlistSize !== undefined && shortlistSize !== null) {
            evt.shortlist_size = shortlistSize;
        }
        if (selectionPath) evt.selection_path = selectionPath;
        return [this.emit(evt as EmitInput)];
    }

    tokenUsage(usage: TokenUsage): StreamEvent[] {
        return [this.emit({ type: 'token_usage', usage })];
    }
}
