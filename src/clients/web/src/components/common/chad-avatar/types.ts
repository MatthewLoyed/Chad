/** Mirrors BuddyEngine modes — shared vocabulary for UI + desktop buddy. */
export type ChadBuddyMode = 'idle' | 'listening' | 'thinking' | 'working' | 'error' | 'speaking';

export interface ChadBuddyModeInput {
    /** Typed reducer turn for the in-flight response. */
    streamTurn: {
        lifecycle: 'idle' | 'running' | 'finished' | 'error';
        parts: ReadonlyArray<{ kind: string; ok?: boolean }>;
    } | null;
    /** Conversation that owns the active stream turn (set when the user sends). */
    streamConversationId: string | null;
    /** Conversation currently open in the chat UI. */
    activeConversationId: string | null;
    status: string;
    /**
     * Phase of a live "Hey Chad" turn, or null when none is in flight.
     * A voice turn belongs to no conversation, so it drives the buddy directly
     * rather than through the conversation-scoped stream state.
     */
    voicePhase: string | null;
}
