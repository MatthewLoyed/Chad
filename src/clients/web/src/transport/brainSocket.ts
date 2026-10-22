/**
 * Browser ↔ Brain WebSocket transport (#188).
 * ===========================================
 *
 * The browser-side counterpart to the desktop `GUIChadLimb` (src/remote/gui_limb.py):
 * it opens a WebSocket to the Brain, authenticates with an AUTH `ChadMessage`
 * (the server authenticates via that message, not the `Authorization` header —
 * which browsers can't set on a WebSocket), and maps every inbound `ChadMessage`
 * to the typed `StreamEvent` stream + the `window.*` runtime handlers that
 * `installStreamHandlers` (src/clients/web/src/runtime/streamRuntime.ts) registers.
 *
 * The message → event mapping ports `GUIChadLimb.handle_message` 1:1, including the
 * speech-buffer interleaving that finalizes a streamed answer block. The Python
 * emitter's IPC-batching is not needed here (no evaluate_js hop), so events are
 * dispatched as they're produced.
 */
import { StreamEventEmitter } from './streamEmitter';
import { getServerUrl, getToken, toWsUrl } from './transportConfig';
import { useAppStore } from '../store';
import type { TokenUsage, FastTrackCommand } from '../store';

// Mirror of src/remote/protocol.py MessageType (only the values that cross this seam).
const MessageType = {
    AUTH: 'auth',
    ERROR: 'error',
    SYSTEM_ERROR: 'system_error',
    USER_INPUT: 'user_input',
    AI_RESPONSE: 'ai_response',
    AI_THOUGHT: 'ai_thought',
    TOOL_REQUEST: 'tool_request',
    TOOL_RESPONSE: 'tool_response',
    AUTONOMOUS_NOTIFICATION: 'autonomous_notification',
    TOKEN_USAGE: 'token_usage',
    EXECUTION_PLAN: 'execution_plan',
    FAST_TRACK_INVOKE: 'fast_track_invoke',
    WORKFLOW_RUN: 'workflow_run',
    TOOL_APPROVAL_REQUEST: 'tool_approval_request',
    TOOL_APPROVAL_RESPONSE: 'tool_approval_response',
    TURN_MODE: 'turn_mode',
    SET_UI_TURN_MODE: 'set_ui_turn_mode',
    CAPABILITY_SUGGESTION: 'capability_suggestion',
} as const;

interface ChadMessage {
    id?: string;
    type: string;
    payload?: Record<string, unknown>;
    auth_token?: string | null;
    timestamp?: number;
}

function uid(): string {
    return Math.random().toString(36).slice(2, 10);
}

/** A turn id that is also a valid primary key.
 *
 * The Brain persists the assistant reply with `message_id = turn_id`, and that column is
 * typed `uuid` in Supabase — so the old `uid() + uid()` (16 base36 chars) made Postgres
 * 400 the INSERT. The user message is written without an id, so it survived while every
 * assistant reply was dropped: PWA chats reloaded with the questions and none of the
 * answers. Local sqlite types the column TEXT and accepted anything, which is why this
 * never showed up in dev.
 *
 * `crypto.randomUUID` needs a secure context; the PWA only ever loads over the Tailscale
 * HTTPS origin, so it is available. The fallback keeps non-secure dev origins working.
 */
function newTurnId(): string {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    if (c && typeof c.getRandomValues === 'function') {
        const b = c.getRandomValues(new Uint8Array(16));
        b[6] = (b[6] & 0x0f) | 0x40; // version 4
        b[8] = (b[8] & 0x3f) | 0x80; // variant 10
        const hex = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    // Last resort: RFC4122-shaped, not cryptographically strong. Still a valid uuid.
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
        const r = (Math.random() * 16) | 0;
        const v = ch === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
}

function makeMessage(type: string, payload: Record<string, unknown> = {}, authToken?: string): string {
    const msg: ChadMessage = {
        id: uid(),
        type,
        payload,
        auth_token: authToken ?? null,
        timestamp: Date.now() / 1000,
    };
    return JSON.stringify(msg);
}

export type ConnectionStatus = 'connecting' | 'authenticating' | 'connected' | 'reconnecting' | 'error' | 'unauthorized';

const INITIAL_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 30000;

/**
 * How long the app can be backgrounded before a resume forces a fresh socket rather than
 * trusting the existing one.
 *
 * iOS tears down WebSockets when a PWA is backgrounded, but frequently never fires
 * `onclose`/`onerror` — `readyState` still reads OPEN against a socket that is actually
 * dead. A resume check gated on `!this.connected` therefore does nothing, and the app sits
 * on a zombie connection showing stale content until it's force-quit. Reconnecting is cheap
 * and idempotent, so past this threshold we just do it unconditionally instead of trying to
 * prove liveness.
 *
 * A couple of seconds is deliberately short: it covers a glance at another app, while still
 * skipping the churn of a reconnect for an instant tab flick.
 */
const STALE_AFTER_HIDDEN_MS = 3000;

/** Minimal mirror of payload shapes the runtime handlers expect. */
type W = typeof window;

export class BrainSocket {
    private ws: WebSocket | null = null;
    private emitter: StreamEventEmitter | null = null;
    private speechBuffer = '';
    private lastEventType: string | null = null;
    private currentTurnId: string | null = null;
    private pendingConversationId: string | null = null;
    private reconnectDelay = INITIAL_RECONNECT_DELAY;
    private reconnectTimer: number | null = null;
    private shouldRun = false;
    // Set once the Brain rejects the current token. Distinguishes "the server told us
    // this credential is bad" from a plain dropped connection: a network blip should
    // keep retrying with backoff, but a rejected token never becomes valid on retry —
    // retrying it just spams the server with the same "Invalid token" over and over
    // and never gives the user a chance to type in a new one. See `open()`/`onclose`.
    private authRejected = false;
    private hasAuthenticated = false;
    private statusListeners = new Set<(s: ConnectionStatus) => void>();
    // When the document last went hidden (PWA backgrounded / screen off), or null while
    // visible. Drives the STALE_AFTER_HIDDEN_MS resume check.
    private hiddenSince: number | null = null;
    // Fires after a reconnect that follows a real interruption, so the app can re-pull
    // server state it may have missed while the socket was down.
    private resyncListeners = new Set<() => void>();
    // True once a connection has been lost and re-established, so the next successful auth
    // is a RE-connect rather than the first one. Only then is a resync warranted.
    private needsResync = false;

    onStatus(cb: (s: ConnectionStatus) => void): () => void {
        this.statusListeners.add(cb);
        return () => this.statusListeners.delete(cb);
    }

    private setStatus(s: ConnectionStatus): void {
        for (const cb of this.statusListeners) {
            try { cb(s); } catch { /* ignore listener errors */ }
        }
    }

    get connected(): boolean {
        return this.ws?.readyState === WebSocket.OPEN;
    }

    /** Open (or re-open) the connection and keep it alive across drops. */
    connect(): void {
        this.shouldRun = true;
        this.authRejected = false;
        // A first connect is not a re-connect: the app is about to load its state normally,
        // so firing a resync here would just duplicate that work.
        this.needsResync = false;
        this.hiddenSince = null;
        this.open();
        // Re-open when the PWA resumes from the iOS app switcher or Android back-stack.
        document.addEventListener('visibilitychange', this._onVisibilityChange);
    }

    /** Force an immediate reconnect attempt (used by the UI "reconnect" button, or after
     *  the user enters a fresh token following an `unauthorized` status). */
    reconnect(): void {
        if (this.reconnectTimer !== null) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        this.reconnectDelay = INITIAL_RECONNECT_DELAY;
        this.shouldRun = true;
        this.authRejected = false;
        try { this.ws?.close(); } catch { /* ignore */ }
        this.open();
    }

    disconnect(): void {
        this.shouldRun = false;
        document.removeEventListener('visibilitychange', this._onVisibilityChange);
        if (this.reconnectTimer !== null) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        try { this.ws?.close(); } catch { /* ignore */ }
        this.ws = null;
    }

    private _onVisibilityChange = (): void => {
        if (document.visibilityState === 'hidden') {
            this.hiddenSince = Date.now();
            return;
        }
        if (document.visibilityState !== 'visible' || !this.shouldRun) return;

        const hiddenFor = this.hiddenSince === null ? 0 : Date.now() - this.hiddenSince;
        this.hiddenSince = null;

        // `!this.connected` alone is not enough: iOS kills the socket on background but
        // often never fires onclose, so readyState still reads OPEN on a dead connection
        // (see STALE_AFTER_HIDDEN_MS). Any meaningful time backgrounded gets a fresh
        // socket regardless of what readyState claims.
        if (!this.connected || hiddenFor >= STALE_AFTER_HIDDEN_MS) {
            this.needsResync = true;
            this.reconnect();
        }
    };

    /** Subscribe to "the connection came back after an interruption" — the cue to re-pull
     *  server state (conversation history, etc.) that may have changed while it was down. */
    onResync(cb: () => void): () => void {
        this.resyncListeners.add(cb);
        return () => this.resyncListeners.delete(cb);
    }

    /** Detach and close whatever socket is currently attached.
     *
     * Handlers are removed BEFORE closing for two reasons:
     *  - a stale `onmessage` keeps firing otherwise, so every frame gets processed twice
     *    and the answer renders doubled ("The user The user wants wants")
     *  - the old socket's `onclose` sets `this.ws = null` unconditionally, which would
     *    null out the reference to the NEW socket if it lands after the swap, and would
     *    also re-enter `scheduleReconnect` and start a reconnect storm.
     */
    private teardownSocket(): void {
        const old = this.ws;
        if (!old) return;
        this.ws = null;
        old.onopen = null;
        old.onmessage = null;
        old.onerror = null;
        old.onclose = null;
        try { old.close(); } catch { /* already closing/closed */ }
    }

    private open(): void {
        if (!getToken()) {
            this.setStatus('error');
            return;
        }
        // Never leave a previous socket attached. `reconnect()` closed one explicitly, but
        // `connect()` and the `scheduleReconnect()` timer did not — and on iOS the old
        // socket is frequently still readyState=OPEN (it kills the connection without ever
        // firing onclose, see STALE_AFTER_HIDDEN_MS). Two live sockets both delivered into
        // the same store, which is why the stream rendered twice once the Brain began
        // fanning turn output out to every connected viewer (#711).
        this.teardownSocket();
        this.setStatus('connecting');
        let ws: WebSocket;
        try {
            ws = new WebSocket(toWsUrl(getServerUrl()));
        } catch {
            this.scheduleReconnect();
            return;
        }
        this.ws = ws;
        this.hasAuthenticated = false;

        ws.onopen = () => {
            if (this.ws !== ws) return;
            this.setStatus('authenticating');
            // client_type tells the Brain to persist this client's turns server-side
            // (issue #189) — the browser has no local ChatHistoryRepository the way the
            // desktop Limb does.
            ws.send(makeMessage(MessageType.AUTH, { client_type: 'browser' }, getToken()));
        };
        ws.onmessage = (ev) => {
            // Belt-and-braces against the doubled stream: only the socket we currently
            // hold may drive the store. teardownSocket() detaches handlers, but a frame
            // already queued on a superseded socket must not be applied a second time.
            if (this.ws !== ws) return;
            let msg: ChadMessage;
            try {
                msg = JSON.parse(ev.data as string) as ChadMessage;
            } catch {
                return;
            }
            void this.handleMessage(msg);
        };
        ws.onerror = () => {
            if (this.ws !== ws) return;
            this.setStatus('error');
        };
        ws.onclose = () => {
            // A superseded socket closing says nothing about the live connection — it must
            // not clear `this.ws` (which now points at the new socket) or trigger a
            // reconnect for a connection that was replaced on purpose.
            if (this.ws !== ws) return;
            this.ws = null;
            if (this.authRejected) {
                // The Brain rejected our token — stop hammering it with the same bad
                // credential. The caller (App) surfaces the login screen again on this
                // status so the user can enter a new token; `connect()`/`reconnect()`
                // re-arm the retry loop once they do.
                this.shouldRun = false;
                this.setStatus('unauthorized');
                return;
            }
            if (this.shouldRun) this.scheduleReconnect();
        };
    }

    private scheduleReconnect(): void {
        if (!this.shouldRun || this.reconnectTimer !== null) return;
        // Reaching here means an established connection dropped, so whatever comes back is
        // a re-connect and the app may have missed server state while it was down.
        this.needsResync = true;
        this.setStatus('reconnecting');
        this.reconnectTimer = window.setTimeout(() => {
            this.reconnectTimer = null;
            this.open();
        }, this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_DELAY);
    }

    private win(): W {
        return window;
    }

    private apply(events: ReturnType<StreamEventEmitter['runStarted']>): void {
        if (events.length) this.win().applyStreamEvent?.(events);
    }

    // ── ChadMessage → StreamEvent + window.* (ports gui_limb.handle_message) ──
    private async handleMessage(msg: ChadMessage): Promise<void> {
        const p = msg.payload || {};
        if (p.delivery === 'telegram' || (typeof p.conversation_id === 'string' && p.conversation_id.includes(':telegram:'))) {
            return;
        }
        const win = this.win();

        switch (msg.type) {
            case MessageType.AUTH: {
                if (p.status === 'success') {
                    this.hasAuthenticated = true;
                    this.reconnectDelay = INITIAL_RECONNECT_DELAY;
                    this.setStatus('connected');
                    win.handleStateUpdate?.('ready');
                    const cmds = (p.fast_track_commands as FastTrackCommand[]) || [];
                    win.handleFastTrackCommands?.(cmds);
                    win.handlePlannerEnabled?.(Boolean(p.planner_enabled));
                    const uiMode = useAppStore.getState().chatTurnMode;
                    this.sendUiTurnMode(uiMode);
                    // Re-auth after a drop: the in-memory view can be arbitrarily stale (a
                    // turn may have finished, or another client may have written, while this
                    // one was backgrounded). Authenticating alone left the old messages on
                    // screen, which is what "it doesn't keep up" looked like on the phone.
                    if (this.needsResync) {
                        this.needsResync = false;
                        for (const cb of this.resyncListeners) {
                            try { cb(); } catch { /* ignore listener errors */ }
                        }
                    }
                } else {
                    this.authRejected = true;
                }
                break;
            }

            case MessageType.ERROR:
            case MessageType.SYSTEM_ERROR: {
                // Only a genuinely rejected credential may set authRejected — the caller
                // (App) responds to the resulting `unauthorized` status by DELETING the
                // stored token, so a false positive here costs the user their pairing.
                //
                // The Brain sends a bare pre-auth ERROR for two very different reasons, and
                // this used to treat both as "bad token": an actually-invalid token, and
                // "still starting up / failed to start" — which is sent AFTER the token was
                // verified. On a phone, resuming into a Brain that was still booting wiped a
                // perfectly good token and forced a re-pair. `code` now disambiguates
                // (server.py's AUTH handler); the legacy text match keeps an older Brain that
                // doesn't send `code` working, and anything unrecognized is treated as
                // transient so the reconnect loop simply retries.
                if (msg.type === MessageType.ERROR && !this.hasAuthenticated) {
                    const code = typeof p.code === 'string' ? p.code : '';
                    const legacyInvalid = !code && (p.error as string) === 'Invalid token';
                    if (code === 'invalid_token' || legacyInvalid) {
                        this.authRejected = true;
                    }
                    break;
                }
                const errorMsg = (p.error as string) || 'Unknown server error';
                if (this.emitter) this.apply(this.emitter.runError(errorMsg));
                this.emitter = null;
                const display = errorMsg.startsWith('⚠️') ? errorMsg : `⚠️ ${errorMsg}`;
                win.addAIMessageToChat?.(display);
                win.handleStateUpdate?.('ready');
                break;
            }

            case MessageType.TOOL_REQUEST: {
                // A browser-hosted Chad has no local (hardware/OS) tool registry, so it
                // cannot service the Brain's local-tool RPC the way the desktop Limb does.
                // Reply with a clean error so the Brain doesn't hang waiting.
                // correlation_id must echo the request id so the Brain matches the reply.
                const resp: ChadMessage = {
                    id: msg.id || uid(),
                    type: MessageType.TOOL_RESPONSE,
                    payload: { success: false, error: 'Local tools are unavailable in the browser client' },
                    auth_token: null,
                    timestamp: Date.now() / 1000,
                };
                this.ws?.send(JSON.stringify(resp));
                break;
            }

            case MessageType.AI_THOUGHT: {
                // Transient phase label (tool selection / model load / thinking). Routed to the
                // live working indicator, NOT the typed reasoning stream — so it never pollutes
                // committed thoughts and works even before the per-turn emitter exists.
                if (p.state === 'status') {
                    // Match gui_limb: phase labels imply the turn is past (or never in)
                    // model_loading — clear the Loading… banner, not only the live text.
                    win.handleStateUpdate?.('thinking');
                    win.handleLiveStatus?.((p.text as string) || '');
                    break;
                }
                if (p.state === 'tools_menu') {
                    const tools = (p.tools as string[]) || [];
                    if (this.emitter && tools.length > 0) {
                        this.apply(this.emitter.toolsSnapshot(
                            tools,
                            (p.shortlist_size as number) ?? null,
                            (p.selection_path as string) ?? null,
                        ));
                    }
                    break;
                }
                if (p.state === 'wait_narrator') {
                    const lines = (p.lines as string[]) || [];
                    if (lines.length > 0) {
                        win.handleWaitNarrator?.(lines);
                    }
                    break;
                }
                this.handleThought(p, msg.id || uid());
                break;
            }

            case MessageType.EXECUTION_PLAN: {
                const content = (p.content as Record<string, unknown>) || {};
                const steps = (content.steps as unknown[]) || [];
                if (this.emitter) {
                    this.apply(this.emitter.planSnapshot(steps as never, (content.goal as string) ?? null));
                }
                break;
            }

            case MessageType.TOOL_APPROVAL_REQUEST: {
                // Session isolation (issue #269): an approval/clarifying-question card
                // belongs to exactly one conversation. Drop it here if the Brain tagged
                // it for a conversation other than the in-flight one — the same guard the
                // final ai_response uses below — so a stray card never lands on the chat
                // the user happens to be viewing. Carry conversation_id downstream so the
                // store can make the same check against the ACTIVE chat at attach time.
                const approvalConvId = (p.conversation_id as string) || null;
                if (
                    approvalConvId &&
                    this.pendingConversationId &&
                    approvalConvId !== this.pendingConversationId
                ) {
                    break;
                }
                win.addToolApprovalToChat?.({
                    token: p.token as string,
                    conversationId: approvalConvId,
                    tool_name: p.tool_name as string | undefined,
                    decision_type: (p.decision_type as 'accept_decline' | 'choice' | 'questions') || 'accept_decline',
                    prompt: p.prompt as string,
                    title: p.title as string | undefined,
                    questions: p.questions as {
                        question: string;
                        header?: string;
                        options: { label: string; description?: string }[];
                        multiSelect?: boolean;
                    }[] | undefined,
                    options: p.options as { id: string; label: string }[] | undefined,
                });
                break;
            }

            case MessageType.AUTONOMOUS_NOTIFICATION: {
                const text = (p.text as string) || '';
                win.handleThinkingEvent?.({ type: 'autonomous_notification', text } as never);
                break;
            }

            case MessageType.TURN_MODE: {
                win.handleTurnModeResolved?.(p as { ui_mode?: 'auto' | 'agent' | 'chat' | 'plan'; mode?: string; source?: string });
                break;
            }

            case MessageType.CAPABILITY_SUGGESTION: {
                win.handleCapabilitySuggestion?.(p as never);
                break;
            }

            case MessageType.TOKEN_USAGE: {
                win.handleTokenUsage?.(p as unknown as TokenUsage);
                if (this.emitter) this.apply(this.emitter.tokenUsage(p as unknown as TokenUsage));
                break;
            }

            case MessageType.AI_RESPONSE: {
                this.handleResponse(p);
                break;
            }

            default:
                break;
        }
    }

    private handleThought(p: Record<string, unknown>, msgId: string): void {
        if (!this.emitter) return;
        // Interleaving: a thought after streamed speech finalizes the open answer block.
        if (this.lastEventType === 'speech' && this.speechBuffer) {
            this.apply(this.emitter.speechFinalize(this.speechBuffer));
            this.speechBuffer = '';
        }
        this.lastEventType = 'thinking';

        const state = p.state as string;
        const text = (p.text as string) || '';
        const agentName = (p.agent_name as string) || null;
        const parentId = (p.parent_id as string) || null;

        if (state === 'browser_progress') {
            try {
                this.apply(this.emitter.browserProgress(JSON.parse(text)));
            } catch { /* Ignore malformed status events; never treat them as reasoning. */ }
        } else if (state === 'thinking' || state === 'thought' || state === 'reasoning' || state === 'thought_reasoning') {
            this.apply(this.emitter.reasoningDelta(text, agentName, parentId));
        } else if (state === 'tool_start') {
            const name = (p.name as string) || 'unknown';
            const params = (p.params as Record<string, unknown>) || {};
            const action = (p.display_text as string) || (p.action as string) || `Executing ${name}...`;
            this.apply(this.emitter.toolStart(msgId, name, params, action, parentId));
        } else if (state === 'tool_end') {
            const ok = p.success !== false;
            this.apply(this.emitter.toolEnd(
                msgId,
                ok,
                p.result,
                (p.download_token as string) ?? null,
                (p.filename as string) ?? null,
                (p.youtube_video_id as string) ?? null,
                (p.youtube_title as string) ?? null,
                p.auto_preview === true ? true : null,
                Boolean(p.awaiting_user),
                (p.map_view as Record<string, unknown>) ?? null,
            ));
        }
    }

    private handleResponse(p: Record<string, unknown>): void {
        const text = (p.text as string) || '';
        const isFragment = p.fragment === true;
        const turnId = (p.turn_id as string) || this.currentTurnId || undefined;
        const convId = (p.conversation_id as string) || null;
        const initiator = (p.initiator as string) || 'user';

        // Autonomous responses (anything not a user/followup turn) have no chat context —
        // route them to the notification panel so they never appear in the active session.
        // Safety net: the Brain already routes these to AUTONOMOUS_NOTIFICATION upstream.
        const isAutonomousTurn = initiator !== 'user' && initiator !== 'followup' && !convId;
        if (isAutonomousTurn) {
            if (!isFragment && text && text.toUpperCase() !== 'HEARTBEAT_OK') {
                this.win().handleThinkingEvent?.({ type: 'autonomous_notification', text } as never);
            }
            if (!isFragment) {
                this.win().handleStateUpdate?.('ready');
                this.emitter = null;
            }
            return;
        }

        if (!this.emitter) return;
        // An approval starts a new turn in the same conversation. A delayed
        // completion from the paused turn must not finish or clear that stream.
        if (turnId && this.currentTurnId && turnId !== this.currentTurnId) return;

        // Session mismatch guard: if the Brain tagged this response with a conversation_id
        // that doesn't match the active session, discard it. This prevents a late response
        // from a previous conversation from landing in the current one.
        if (convId && this.pendingConversationId && convId !== this.pendingConversationId) {
            return;
        }

        if (isFragment) {
            if (text) {
                this.lastEventType = 'speech';
                this.speechBuffer += text;
                this.apply(this.emitter.textDelta(text));
            }
            return;
        }

        // Final turn completion: flush any remaining streamed speech, then finish.
        if (this.speechBuffer) {
            this.apply(this.emitter.speechFinalize(this.speechBuffer));
            this.speechBuffer = '';
        }

        const turnDurationMs = (p.turn_duration_ms as number) || undefined;
        this.apply(this.emitter.runFinished(text, undefined, turnDurationMs));

        const metadata: Record<string, unknown> = {
            initiator,
            turn_duration_ms: p.turn_duration_ms,
            thinking_duration_ms: p.thinking_duration_ms,
            prompt_tokens: p.prompt_tokens,
            completion_tokens: p.completion_tokens,
            total_tokens: p.total_tokens,
            reasoning_tokens: p.reasoning_tokens,
            context_window: p.context_window,
            ttft: p.ttft,
            stop_reason: p.stop_reason,
            speed: p.speed,
        };

        // Use the conversation_id from the Brain's payload when available — it is the
        // authoritative source (set from the USER_INPUT that started this turn). Fall
        // back to pendingConversationId only when the server didn't send one.
        const targetConvId = convId || this.pendingConversationId;

        // Finalize the bubble (sources thoughts from the just-finished streamTurn,
        // then clears it — same ordering as the desktop push_ai_response).
        this.win().addAIMessageToChat?.(text, targetConvId, turnId, metadata);

        this.win().handleStateUpdate?.('ready');
        this.emitter = null;
        this.lastEventType = null;
    }

    sendUiTurnMode(uiMode: string): boolean {
        if (!this.connected || !uiMode) return false;
        this.ws!.send(makeMessage(MessageType.SET_UI_TURN_MODE, { ui_mode: uiMode }));
        return true;
    }

    // ── Outbound (ports ChadLimb.send_* / GUIChadLimb.send_input) ──
    async sendInput(
        text: string,
        contentBlocks?: unknown[] | null,
        conversationId?: string | null,
        category?: string | null,
        thinkingEnabled?: boolean | null,
        imageBase64?: string | null,
        domain?: string | null,
        retry?: boolean | null,
        keepCount?: number | null,
    ): Promise<boolean> {
        if (!this.connected) return false;
        const turnId = newTurnId();
        this.currentTurnId = turnId;
        this.pendingConversationId = conversationId ?? this.pendingConversationId;
        useAppStore.getState().setStreamConversationId(conversationId ?? this.pendingConversationId);

        // Start a fresh typed-stream turn (drives the React render).
        this.emitter = new StreamEventEmitter(turnId);
        this.speechBuffer = '';
        this.lastEventType = null;
        this.apply(this.emitter.runStarted(turnId));

        useAppStore.getState().beginTurnWait();

        const payload: Record<string, unknown> = { text, turn_id: turnId };
        if (imageBase64) payload.image_base64 = imageBase64;
        if (contentBlocks) payload.content_blocks = contentBlocks;
        if (conversationId) payload.conversation_id = conversationId;
        if (category) payload.category = category;
        // Domain agent for this turn. Distinct from `category` above, which is the delivery
        // mode ('voice'/'contact') — do not merge them. Only seeds a BRAND-NEW conversation:
        // once a row exists the Brain reads the domain off it and ignores this field.
        if (domain) payload.domain = domain;
        if (thinkingEnabled !== null && thinkingEnabled !== undefined) payload.thinking_enabled = thinkingEnabled;
        if (retry) payload.retry = true;
        if (typeof keepCount === 'number') payload.keep_count = keepCount;
        payload.delivery = category === 'voice' ? 'voice' : 'gui';

        this.ws!.send(makeMessage(MessageType.USER_INPUT, payload));
        return true;
    }

    sendFastTrackInvoke(alias: string): boolean {
        if (!this.connected) return false;
        this.ws!.send(makeMessage(MessageType.FAST_TRACK_INVOKE, { alias }));
        return true;
    }

    sendWorkflowRun(workflowId: string): boolean {
        if (!this.connected) return false;
        this.ws!.send(makeMessage(MessageType.WORKFLOW_RUN, { workflow_id: workflowId }));
        return true;
    }

    /** User cancelled — close the in-flight typed turn and stop accepting stream events.
     *
     * Emits `run_finished(stop_reason='user_cancelled')` FIRST, before dropping the emitter.
     * On the desktop this frame is synthesized by the Limb (`gui_limb.abort_active_turn` ->
     * `StreamEventEmitter.run_cancelled`), and `store.abortActiveTurn` relies on it arriving:
     * it deliberately leaves `streamTurn` alone so the normal end-of-turn path can mark
     * lifecycle='finished' and commit the partial bubble.
     *
     * On the browser transport there is no Limb, and the Brain sends nothing terminal for a
     * cancelled turn — only a raw `{type:'status_update'}` frame the client has no handler
     * for. So the turn stayed lifecycle='running' forever: the thinking stream stopped, but
     * the stop button and status kept looking live. brainSocket IS the Limb here, so it owes
     * the same frame.
     */
    abortActiveTurn(): void {
        if (this.emitter) {
            // Flush any half-spoken sentence first, mirroring the desktop's abort ordering,
            // so the committed bubble keeps text the user already saw.
            if (this.speechBuffer) this.apply(this.emitter.speechFinalize(this.speechBuffer));
            // `undefined` response (not '') matches Python run_cancelled(response=None):
            // it must not overwrite the streamed text with an empty answer.
            this.apply(this.emitter.runFinished(undefined, 'user_cancelled'));
        }
        this.emitter = null;
        this.speechBuffer = '';
        this.lastEventType = null;
        useAppStore.getState().abortActiveTurn();
        this.win().handleStateUpdate?.('ready');
    }

    /** Resolve a generic tool-approval card, then submit the decision as a follow-up turn. */
    async resolveToolApproval(token: string, decision: string, displayText?: string): Promise<boolean> {
        if (!this.connected) return false;
        this.ws!.send(makeMessage(MessageType.TOOL_APPROVAL_RESPONSE, { token, decision }));
        await this.sendInput(displayText ?? decision, null, this.pendingConversationId);
        return true;
    }
}

/** Process-wide singleton — one Brain connection per browser tab. */
export const brainSocket = new BrainSocket();
