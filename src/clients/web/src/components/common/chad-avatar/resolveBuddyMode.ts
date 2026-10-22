import type { ChadBuddyMode, ChadBuddyModeInput } from './types';
import { ownsActiveConversation } from '../../../session/sessionGuard';

function hasOpenTools(streamTurn: NonNullable<ChadBuddyModeInput['streamTurn']>): boolean {
    return streamTurn.parts.some((part) => part.kind === 'tool' && part.ok === undefined);
}

/**
 * Derive the buddy animation mode for the **active chat only**.
 * Ignores global status / stream state that belongs to another conversation
 * (autonomous runs, background chats, heartbeat turns).
 */
/** Buddy mode for a live voice turn, or null when no voice turn is in flight. */
function voiceBuddyMode(phase: string | null): ChadBuddyMode | null {
    switch (phase) {
        case 'listening': return 'listening';
        case 'transcribing':
        case 'heard':
        case 'thinking': return 'thinking';
        case 'speaking': return 'speaking';
        case 'error': return 'error';
        default: return null; // 'armed' / null — nothing in flight
    }
}

export function resolveBuddyMode(input: ChadBuddyModeInput): ChadBuddyMode {
    const {
        streamTurn,
        streamConversationId,
        activeConversationId,
        status,
        voicePhase,
    } = input;

    // A voice turn wins: it's happening right now and, unlike a chat turn, isn't
    // scoped to any conversation, so the conversation guard below can't see it.
    const voiceMode = voiceBuddyMode(voicePhase);
    if (voiceMode) return voiceMode;

    const scopedToActiveChat = ownsActiveConversation(streamConversationId, activeConversationId);

    if (!scopedToActiveChat) {
        return 'idle';
    }

    if (streamTurn?.lifecycle === 'error') {
        return 'error';
    }

    if (streamTurn) {
        if (hasOpenTools(streamTurn)) {
            return 'working';
        }
        if (streamTurn.lifecycle === 'running') {
            return 'thinking';
        }
    }

    if (status === 'speaking') return 'speaking';
    if (status === 'listening') return 'listening';
    if (
        status === 'executing' ||
        status === 'thinking' ||
        status === 'active' ||
        status === 'model_loading'
    ) {
        return 'thinking';
    }

    return 'idle';
}
