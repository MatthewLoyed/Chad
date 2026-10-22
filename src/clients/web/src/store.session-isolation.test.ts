/**
 * Session-isolation invariants for the chat store (issue #269).
 * =============================================================
 *
 * These tests MANDATE that an approval / clarifying-question card never bleeds
 * across conversations. They are deliberately behavioural (drive the real store
 * actions the app uses) so a future refactor that reintroduces a global,
 * unscoped approval fails here immediately. The standing invariant is:
 *
 *     a correct flow produces ZERO session-guard violations, and any
 *     cross-conversation attach is both dropped AND recorded.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from './store';
import type { ToolApprovalPayload } from './utils/tool-approval';
import { getSessionViolations, resetSessionViolations } from './session/sessionGuard';

const approvalFor = (conversationId: string | null): ToolApprovalPayload => ({
    token: `tok-${conversationId}`,
    conversationId,
    decision_type: 'questions',
    prompt: 'Which approach?',
    questions: [
        {
            question: 'Which approach?',
            options: [{ label: 'A' }, { label: 'B' }],
        },
    ],
});

/** Reset the store slices these tests touch to a known baseline. */
function resetStore() {
    useAppStore.setState({
        chatMessages: [],
        activeConversationId: null,
        pendingToolApproval: null,
        streamTurn: null,
        activeTurn: null,
        streamConversationId: null,
        streamingResponse: null,
    });
}

beforeEach(() => {
    resetStore();
    resetSessionViolations();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('buffered approval attaches only to its own conversation', () => {
    it('attaches when the finalized bubble belongs to the same chat', () => {
        const s = useAppStore.getState();
        s.setActiveConversation('conv-a');
        s.setPendingToolApproval(approvalFor('conv-a'));

        s.addChatMessage('ai', 'Here is my answer');

        const msgs = useAppStore.getState().chatMessages;
        const last = msgs[msgs.length - 1];
        expect(last.actions?.type).toBe('tool_approval');
        expect(useAppStore.getState().pendingToolApproval).toBeNull();
        expect(getSessionViolations()).toHaveLength(0);
    });

    it('does NOT attach when the user has navigated to a different chat', () => {
        const s = useAppStore.getState();
        // Card was raised in conv-a, but the user is now viewing conv-b.
        s.setPendingToolApproval(approvalFor('conv-a'));
        s.setActiveConversation('conv-b');

        s.addChatMessage('ai', 'Unrelated answer in conv-b');

        const msgs = useAppStore.getState().chatMessages;
        const last = msgs[msgs.length - 1];
        expect(last.actions).toBeUndefined();
        // Stale card is dropped, and the leak attempt is recorded.
        expect(useAppStore.getState().pendingToolApproval).toBeNull();
        expect(getSessionViolations()).toHaveLength(1);
        expect(getSessionViolations()[0]).toMatchObject({ incoming: 'conv-a', active: 'conv-b' });
    });
});

describe('attachToolApprovalToLastAIMessage respects the conversation', () => {
    it('attaches a matching card to the last AI bubble', () => {
        const s = useAppStore.getState();
        s.setActiveConversation('conv-a');
        s.addChatMessage('ai', 'prior answer');

        s.attachToolApprovalToLastAIMessage(approvalFor('conv-a'));

        const msgs = useAppStore.getState().chatMessages;
        expect(msgs[msgs.length - 1].actions?.type).toBe('tool_approval');
        expect(getSessionViolations()).toHaveLength(0);
    });

    it('refuses to stamp a card onto a different conversation', () => {
        const s = useAppStore.getState();
        s.setActiveConversation('conv-b');
        s.addChatMessage('ai', 'conv-b history');

        s.attachToolApprovalToLastAIMessage(approvalFor('conv-a'));

        const msgs = useAppStore.getState().chatMessages;
        expect(msgs[msgs.length - 1].actions).toBeUndefined();
        expect(getSessionViolations()).toHaveLength(1);
    });
});

describe('resetLiveTurnState tears everything down (chat switch / new chat)', () => {
    it('clears the buffered approval and all live turn state', () => {
        const s = useAppStore.getState();
        s.setActiveConversation('conv-a');
        s.setPendingToolApproval(approvalFor('conv-a'));
        useAppStore.setState({
            streamTurn: { lifecycle: 'running' } as never,
            activeTurn: { events: [] } as never,
            streamingResponse: 'partial…',
        });

        s.resetLiveTurnState();

        const after = useAppStore.getState();
        expect(after.pendingToolApproval).toBeNull();
        expect(after.streamTurn).toBeNull();
        expect(after.activeTurn).toBeNull();
        expect(after.streamingResponse).toBeNull();
    });

    it('after teardown, a finalized bubble in the NEXT chat gets no stale card', () => {
        const s = useAppStore.getState();
        // Card raised in conv-a…
        s.setActiveConversation('conv-a');
        s.setPendingToolApproval(approvalFor('conv-a'));
        // …user switches to conv-b (App calls resetLiveTurnState on switch)…
        s.resetLiveTurnState();
        s.setActiveConversation('conv-b');

        // …and a fresh turn finalizes in conv-b.
        s.addChatMessage('ai', 'clean answer');

        const msgs = useAppStore.getState().chatMessages;
        expect(msgs[msgs.length - 1].actions).toBeUndefined();
        // No leak attempt was even possible — the card was gone before finalize.
        expect(getSessionViolations()).toHaveLength(0);
    });
});
