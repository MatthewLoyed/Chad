/**
 * Brain→Limb→React stream runtime.
 * ================================
 *
 * Registers the `window.*` bridge handlers that main_window.py drives via
 * evaluate_js. Live turn content (thinking / tools / plan / answer) flows through
 * the TYPED protocol: `window.applyStreamEvent` → the streamReducer → the
 * `streamTurn` store slice → ChatStream renders from it. The legacy imperative-DOM
 * append path (token queues, rAF drain, raw-text refs) has been removed.
 *
 * The remaining `window.handle*` handlers carry the out-of-band signals the typed
 * stream doesn't: status, status text, token telemetry, audio level, message
 * finalize/persist, and the legacy thinking channel reduced to just autonomous
 * notifications + model-loading (which the typed emitter doesn't produce).
 *
 * Handlers read the store via getState() so they never capture stale closures.
 */
import { partsToThinkingEvents } from '../protocol/streamReducer';
import { maybeAutoPreviewFromStreamEvents } from '../components/data-display/autoPreviewArtifact';
import { useAppStore, type ThinkingEvent, type AppStatus, type Conversation, type TokenUsage, type FastTrackCommand, type ChatTurnMode } from '../store';

/** Flatten Anthropic-style content blocks / JSON strings down to display text. */
export function extractTextFromContent(content: unknown): string {
    if (typeof content === 'string') {
        if (content.startsWith('[') || content.startsWith('{')) {
            try {
                const parsed: unknown = JSON.parse(content);
                return extractTextFromContent(parsed);
            } catch { /* ignore */ }
        }
        return content;
    }
    if (Array.isArray(content)) {
        return content
            .map((block) => {
                const b = block as { type?: string; text?: string };
                if (b && typeof b === 'object') {
                    if (b.type === 'text') return b.text || '';
                    if (b.type === 'thought') return ''; // thoughts handled separately
                    return `[${b.type}]`;
                }
                return '';
            })
            .join('');
    }
    return content ? JSON.stringify(content) : '';
}

/**
 * Register the window.* bridge handlers main_window.py drives. Returns an
 * uninstall function (used by the DOM harness to reset between replays).
 */
export function installStreamHandlers(): () => void {
    const store = () => useAppStore.getState();

    window.handleStateUpdate = (state: string) => {
        store().setStatus(state as AppStatus);
        // Keep human-readable status text — don't overwrite with the raw enum.
    };

    // The legacy thinking channel now only carries signals the typed stream does
    // NOT produce: autonomous notifications (rendered as toasts from thinkingEvents)
    // and model_loading (drives the active-model display). Live reasoning/tool/
    // speech render from the typed streamTurn instead, so they're ignored here.
    window.handleThinkingEvent = (event: ThinkingEvent) => {
        if (event.type === 'autonomous_notification' || event.type === 'model_loading') {
            store().addThinkingEvent(event);
            // Real load signal from the Brain — only path that should light the
            // Loading… banner (App.tsx no longer optimistic-sets model_loading).
            if (event.type === 'model_loading') {
                store().setStatus('model_loading');
            }
        }
    };

    window.handleStatusText = (text: string) => store().setStatusText(text);

    // Transient pre-first-token phase label (backend ai_thought state='status'): drives the
    // live working indicator so the model-load / tool-selection gap doesn't look frozen.
    // Also leave model_loading once the Brain is emitting phase labels — residency is
    // already handled server-side; a stuck Loading… banner is never correct here.
    window.handleLiveStatus = (text: string) => {
        store().setLiveStatus(text);
        if (store().status === 'model_loading') {
            store().setStatus('thinking');
        }
    };

    window.handleWaitNarrator = (lines: string[]) => store().setWaitNarratorLines(lines);

    window.handleTokenUsage = (usage: TokenUsage) => store().setTokenUsage(usage);

    window.handleAudioLevel = (level: number) => store().setAudioLevel(level);

    // Allowlisted '/<alias>' fast-track commands, pushed once at AUTH success.
    window.handleFastTrackCommands = (commands: FastTrackCommand[]) => {
        store().setFastTrackCommands(commands || []);
    };

    // planner.enabled capability — hides plan mode in the composer when false.
    window.handlePlannerEnabled = (enabled: boolean) => {
        store().setPlannerEnabled(Boolean(enabled));
    };

    window.handleTurnModeResolved = (payload: { ui_mode?: ChatTurnMode; mode?: string; source?: string }) => {
        const uiMode = payload?.ui_mode;
        if (uiMode && uiMode !== 'auto') {
            store().setChatTurnMode(uiMode);
        }
    };

    window.addAIMessageToChat = (
        text: string,
        conversationData?: Conversation | string | null,
        messageId?: string,
        metadata?: Record<string, unknown> | null,
    ) => {
        // Thoughts for the finalized bubble come from the typed turn (addChatMessage
        // sources streamTurn when no forcedThoughts are given), then streamTurn is
        // cleared as part of the same finalize.
        const cleanText = extractTextFromContent(text);

        // Which conversation did this turn belong to?
        const finishedConvId = typeof conversationData === 'object' && conversationData?.id
            ? conversationData.id
            : (typeof conversationData === 'string' ? conversationData : null);
        const activeConvId = store().activeConversationId;

        // If the turn finished while the user is viewing a DIFFERENT chat, do NOT
        // append its bubble to the visible message list or yank the active
        // conversation. Just retire the live turn slot; the answer is persisted
        // server-side and shows up when the user navigates back. Still refresh the
        // sidebar row so its updated_at/title move.
        if (finishedConvId && activeConvId && finishedConvId !== activeConvId) {
            store().finalizeBackgroundTurn();
            if (conversationData && typeof conversationData === 'object' && conversationData.id) {
                store().upsertConversation(conversationData);
            } else if (typeof conversationData === 'string') {
                store().updateConversation(conversationData, { updated_at: new Date().toISOString() });
            }
            return;
        }

        store().addChatMessage('ai', cleanText, undefined, undefined, messageId, metadata ?? undefined);
        if (conversationData) {
            if (typeof conversationData === 'object' && conversationData.id) {
                store().upsertConversation(conversationData);
                store().setActiveConversation(conversationData.id);
            } else if (typeof conversationData === 'string') {
                store().setActiveConversation(conversationData);
                store().updateConversation(conversationData, { updated_at: new Date().toISOString() });
            }
        }
    };

    window.addUserMessageToChat = (text: string, conversationId?: string) => {
        const cleanText = extractTextFromContent(text);
        store().addChatMessage('user', cleanText);
        if (conversationId) store().setActiveConversation(conversationId);
    };

    // The typed-stream sink: the Python emitter (gui_limb push_stream_event) pushes
    // a batch of StreamEvents here; we fold them into streamTurn via the reducer.
    window.applyStreamEvent = (events) => {
        store().applyStreamEvents(events);
        maybeAutoPreviewFromStreamEvents(events);
    };

    window.abortActiveTurn = () => {
        store().abortActiveTurn();
    };

    // Voice mode ("Hey Chad") gets its own channel rather than riding
    // handleStateUpdate/handleStatusText. Those are shared with the chat turn
    // lifecycle — brainSocket pushes handleStateUpdate('ready') at several
    // turn-end points — so voice state written there is wiped by unrelated chat
    // activity. See issue #654.
    window.handleVoiceState = (payload) => store().applyVoiceState(payload);
    window.handleVoiceAvailability = (payload) => store().setVoiceAvailability(payload);

    return () => {
        delete window.handleStateUpdate;
        delete window.handleThinkingEvent;
        delete window.handleStatusText;
        delete window.handleLiveStatus;
        delete window.handleWaitNarrator;
        delete window.handleTokenUsage;
        delete window.handleAudioLevel;
        delete window.handleFastTrackCommands;
        delete window.handlePlannerEnabled;
        delete window.handleTurnModeResolved;
        delete window.addAIMessageToChat;
        delete window.addUserMessageToChat;
        delete window.applyStreamEvent;
        delete window.handleVoiceState;
        delete window.handleVoiceAvailability;
        delete window.abortActiveTurn;
    };
}

// Re-export so existing imports keep working; the adapter lives in the reducer.
export { partsToThinkingEvents };
