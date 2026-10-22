import { useAppStore } from '../../../store';
import { resolveBuddyMode } from './resolveBuddyMode';
import type { ChadBuddyMode } from './types';

/** Live buddy mode for the conversation open in chat — not global Brain activity. */
export function useChadBuddyMode(): ChadBuddyMode {
    return useAppStore((state) =>
        resolveBuddyMode({
            streamTurn: state.streamTurn,
            streamConversationId: state.streamConversationId,
            activeConversationId: state.activeConversationId,
            status: state.status,
            voicePhase: state.voiceTurn?.phase ?? null,
        }),
    );
}
