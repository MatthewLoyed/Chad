import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getActiveChatBusy, getActiveChatStreamTurn, useAppStore } from './store';
import type { TurnState } from './protocol/streamReducer';
import { getSessionViolations, resetSessionViolations } from './session/sessionGuard';

function resetStore() {
    useAppStore.setState({
        chatMessages: [],
        activeConversationId: null,
        streamTurn: null,
        streamConversationId: null,
        activeTurn: null,
        streamingResponse: null,
        status: 'ready',
        statusText: '',
        pendingToolApproval: null,
        liveStatus: null,
        liveNarratorLines: null,
    });
}

/**
 * Mirrors App.tsx handleChatSelect teardown. A turn still streaming is PRESERVED
 * across the switch (backgrounded, not aborted) — only an idle chat is torn down.
 * Isolation then holds at render time (owner === active), not by discarding events.
 */
function simulateChatSwitch(targetConversationId: string) {
    const s = useAppStore.getState();
    const liveTurnRunning = s.streamTurn?.lifecycle === 'running';
    s.setActiveConversation(targetConversationId);
    if (!liveTurnRunning) {
        s.resetLiveTurnState();
        s.clearThinkingEvents();
    }
}

const runningTurn: TurnState = {
    turn_id: 'turn-a',
    lifecycle: 'running',
    parts: [{ kind: 'reasoning', id: 'r1', text: '...', ended: false }],
    usage: null,
    response: null,
    error: null,
    lastSeq: 1,
    seqGap: false,
};

beforeEach(() => {
    resetStore();
    resetSessionViolations();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('stop button session isolation', () => {
    it('keeps a running turn alive on switch but shows Send (not Stop) in the new chat', () => {
        useAppStore.setState({
            activeConversationId: 'conv-a',
            streamConversationId: 'conv-a',
            streamTurn: runningTurn,
            status: 'thinking',
        });

        simulateChatSwitch('conv-b');

        const after = useAppStore.getState();
        expect(after.activeConversationId).toBe('conv-b');
        // The turn is BACKGROUNDED, not destroyed — it keeps its owner (conv-a)…
        expect(after.streamTurn).not.toBeNull();
        expect(after.streamConversationId).toBe('conv-a');
        // …but is hidden and non-busy in the chat now on screen (conv-b).
        expect(getActiveChatStreamTurn(after)).toBeNull();
        expect(getActiveChatBusy(after)).toBe(false);
    });

    it('ignores backend status IPC after switch when no live turn is scoped to this chat', () => {
        useAppStore.setState({
            activeConversationId: 'conv-a',
            streamConversationId: 'conv-a',
            streamTurn: runningTurn,
            status: 'thinking',
        });

        simulateChatSwitch('conv-b');

        // Same effect as window.handleStateUpdate from gui_limb (no conversation guard).
        useAppStore.getState().setStatus('executing');

        const after = useAppStore.getState();
        expect(after.activeConversationId).toBe('conv-b');
        expect(after.status).toBe('executing');
        expect(getActiveChatBusy(after)).toBe(false);
    });

    it('keeps folding backend stream events for a backgrounded turn, but hides them from the other chat', () => {
        useAppStore.setState({
            activeConversationId: 'conv-a',
            streamConversationId: 'conv-a',
            streamTurn: runningTurn,
            status: 'thinking',
        });

        simulateChatSwitch('conv-b');

        // Events for the still-running conv-a turn keep arriving while conv-b is on screen.
        useAppStore.getState().applyStreamEvents([{ type: 'reasoning_delta', part_id: 'r1', delta: 'more', seq: 2 }]);

        const after = useAppStore.getState();
        expect(after.activeConversationId).toBe('conv-b');
        // The turn keeps accumulating under its owner (conv-a) — NOT dropped…
        expect(after.streamConversationId).toBe('conv-a');
        expect(after.streamTurn).not.toBeNull();
        // …and stays hidden / non-busy in conv-b. Isolation holds at render time.
        expect(getActiveChatStreamTurn(after)).toBeNull();
        expect(getActiveChatBusy(after)).toBe(false);
        expect(getSessionViolations()).toHaveLength(0);
    });

    it('shows an idle composer on the new-chat path while a prior turn keeps running', () => {
        useAppStore.setState({
            activeConversationId: 'conv-a',
            streamConversationId: 'conv-a',
            streamTurn: runningTurn,
            status: 'model_loading',
        });

        // Mirror App.tsx handleNewChat: a running turn is preserved (backgrounded),
        // not aborted; the fresh splash composer is idle because no turn owns it.
        const s = useAppStore.getState();
        const liveTurnRunning = s.streamTurn?.lifecycle === 'running';
        if (!liveTurnRunning) {
            s.resetLiveTurnState();
            s.clearThinkingEvents();
        }
        s.setActiveConversation(null);

        const after = useAppStore.getState();
        expect(after.activeConversationId).toBeNull();
        expect(after.streamTurn).not.toBeNull();
        expect(after.streamConversationId).toBe('conv-a');
        expect(getActiveChatBusy(after)).toBe(false);
    });

    it('should show Send (not Stop) in conv-b after leaving an in-flight turn in conv-a', () => {
        useAppStore.setState({
            activeConversationId: 'conv-a',
            streamConversationId: 'conv-a',
            streamTurn: runningTurn,
            status: 'thinking',
        });

        simulateChatSwitch('conv-b');

        // Desired UX: destination chat input is idle even if conv-a keeps running server-side.
        expect(getActiveChatBusy(useAppStore.getState())).toBe(false);
    });
});
