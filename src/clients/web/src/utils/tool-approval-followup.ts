import { partsToThinkingEvents, type TurnState } from '../protocol/streamReducer';
import { normalizeTurnEvents } from '../reducers/turnEventReducer';
import type { ActiveTurn, ChatMessage } from '../store';
import {
    buildToolApprovalActions,
    type ToolApprovalActions,
    type ToolApprovalPayload,
} from './tool-approval';
import {
    MAX_CHAT_MESSAGES,
    MAX_MESSAGES_WITH_THOUGHTS,
    pruneEvent,
} from './eventPruning';
import type { ClarifyingQuestion } from '../components/data-display/ClarifyingQuestionsCard';

export type FollowUpState = {
    chatMessages: ChatMessage[];
    streamTurn: TurnState | null;
    streamConversationId: string | null;
    activeConversationId?: string | null;
    pendingToolApproval: ToolApprovalPayload | null;
    skipNextAiTurnFinalizeId: string | null;
    turnStartTime?: number;
    thinkingStartTime?: number | null;
    thinkingEndTime?: number | null;
    liveStatus?: string | null;
    liveStatusUpdatedAt?: number | null;
    liveNarratorLines?: string[] | null;
    activeTurn?: ActiveTurn | null;
};

function findLastUnresolvedApprovalIdx(messages: ChatMessage[]): number {
    for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg.role === 'ai' && msg.actions?.type === 'tool_approval' && !msg.actions.resolved) {
            return i;
        }
    }
    return -1;
}

function resolvedActions(
    actions: ToolApprovalActions,
    decision: string,
): ToolApprovalActions {
    return { ...actions, resolved: true, userChoice: decision };
}

function trimMessages(messages: ChatMessage[]): ChatMessage[] {
    let updated = messages.length > MAX_CHAT_MESSAGES
        ? messages.slice(-MAX_CHAT_MESSAGES)
        : messages;

    if (updated.length > MAX_MESSAGES_WITH_THOUGHTS) {
        const cutOff = updated.length - MAX_MESSAGES_WITH_THOUGHTS;
        updated = updated.map((msg, idx) =>
            idx < cutOff && msg.thoughts ? { ...msg, thoughts: undefined } : msg,
        );
    }
    return updated;
}

function questionsFromApproval(
    pending: ToolApprovalPayload | null,
    actions: ToolApprovalActions | undefined,
): ClarifyingQuestion[] {
    if (pending?.questions?.length) return pending.questions;
    if (actions?.type === 'tool_approval' && actions.questions?.length) {
        return actions.questions;
    }
    return [];
}

function isQuestionsDecision(
    pending: ToolApprovalPayload | null,
    actions: ToolApprovalActions | undefined,
): boolean {
    if (pending?.decision_type === 'questions') return true;
    if (actions?.type === 'tool_approval' && actions.decisionType === 'questions') return true;
    return questionsFromApproval(pending, actions).length > 0;
}

function findLastResolvedApprovalActions(
    messages: ChatMessage[],
): ToolApprovalActions | undefined {
    for (let i = messages.length - 1; i >= 0; i--) {
        const actions = messages[i].actions;
        if (messages[i].role === 'ai' && actions?.type === 'tool_approval' && actions.resolved) {
            return actions;
        }
    }
    return undefined;
}

/**
 * Commit a paused tool-approval turn into chat history and append the user's
 * answer before resolve_tool_approval starts a fresh stream (run_started), which
 * would otherwise wipe the in-flight streamTurn without persisting it.
 *
 * Clarifying questions land as a structured user-side Q&A card (muted question +
 * bold answer). Accept/decline and plain choice keep a normal user text bubble.
 */
export function applyToolApprovalFollowUp(
    state: FollowUpState,
    decision: string,
    displayText?: string,
): FollowUpState {
    const display = displayText ?? decision;
    const pending = state.pendingToolApproval;
    const chatMessages = [...state.chatMessages];
    let skipNextAiTurnFinalizeId = state.skipNextAiTurnFinalizeId ?? null;

    const liveThoughts = state.streamTurn?.parts?.length
        ? normalizeTurnEvents(partsToThinkingEvents(state.streamTurn).map(pruneEvent))
        : undefined;

    const approvalIdx = findLastUnresolvedApprovalIdx(chatMessages);
    const priorActions =
        approvalIdx >= 0 && chatMessages[approvalIdx].actions?.type === 'tool_approval'
            ? (chatMessages[approvalIdx].actions as ToolApprovalActions)
            : undefined;

    if (liveThoughts?.length) {
        const turnId = state.streamTurn?.turn_id ?? null;
        if (turnId) {
            skipNextAiTurnFinalizeId = turnId;
        }

        const baseActions = pending
            ? resolvedActions(buildToolApprovalActions(pending), decision)
            : priorActions
              ? resolvedActions(priorActions, decision)
              : undefined;

        if (approvalIdx >= 0 && baseActions) {
            chatMessages[approvalIdx] = {
                ...chatMessages[approvalIdx],
                thoughts: liveThoughts,
                actions: baseActions,
            };
        } else if (baseActions) {
            chatMessages.push({
                id: `msg-${Date.now()}-approval`,
                role: 'ai',
                text: '',
                timestamp: new Date(),
                thoughts: liveThoughts,
                actions: baseActions,
            });
        } else {
            chatMessages.push({
                id: `msg-${Date.now()}-approval`,
                role: 'ai',
                text: '',
                timestamp: new Date(),
                thoughts: liveThoughts,
            });
        }
    } else if (approvalIdx >= 0) {
        const msg = chatMessages[approvalIdx];
        if (msg.actions?.type === 'tool_approval') {
            chatMessages[approvalIdx] = {
                ...msg,
                actions: resolvedActions(msg.actions, decision),
            };
        }
    }

    const resolvedAiActions = findLastResolvedApprovalActions(chatMessages) ?? priorActions;
    const questions = questionsFromApproval(pending, resolvedAiActions);
    const asClarifying = isQuestionsDecision(pending, resolvedAiActions) && questions.length > 0;

    chatMessages.push({
        id: `msg-${Date.now()}-user`,
        role: 'user',
        text: display,
        timestamp: new Date(),
        clarifyingReply: asClarifying
            ? { questions, userChoice: decision }
            : undefined,
    });

    return {
        ...state,
        chatMessages: trimMessages(chatMessages),
        streamTurn: null,
        // Desktop resumes directly through the bridge, so no frontend sendInput
        // runs to reclaim ownership. Keep the continuation visible in its chat.
        streamConversationId: state.streamConversationId ?? pending?.conversationId ?? state.activeConversationId ?? null,
        pendingToolApproval: null,
        skipNextAiTurnFinalizeId,
        turnStartTime: Date.now(),
        thinkingStartTime: null,
        thinkingEndTime: null,
        liveStatus: null,
        liveStatusUpdatedAt: null,
        liveNarratorLines: null,
        activeTurn: null,
    };
}
