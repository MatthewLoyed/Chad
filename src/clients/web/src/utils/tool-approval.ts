import type { ChatMessage, ThinkingEvent } from '../store';
import type { ClarifyingQuestion } from '../components/data-display/ClarifyingQuestionsCard';

export interface ToolApprovalPayload {
    token: string;
    /**
     * Conversation this approval belongs to (issue #269). Null only for legacy
     * emitters that predate session stamping. The store refuses to attach the
     * card to any chat but this one — see attachToolApprovalToLastAIMessage.
     */
    conversationId?: string | null;
    tool_name?: string;
    params?: Record<string, unknown>;
    decision_type?: 'accept_decline' | 'choice' | 'questions';
    prompt: string;
    title?: string;
    questions?: {
        question: string;
        header?: string;
        options: { label: string; description?: string }[];
        multiSelect?: boolean;
    }[];
    options?: { id: string; label: string }[];
}

export type ToolApprovalActions = Extract<NonNullable<ChatMessage['actions']>, { type: 'tool_approval' }>;

export function buildToolApprovalActions(payload: ToolApprovalPayload): ToolApprovalActions {
    return {
        type: 'tool_approval',
        token: payload.token,
        tool_name: payload.tool_name,
        params: payload.params,
        decisionType: payload.decision_type || 'accept_decline',
        prompt: payload.prompt,
        title: payload.title,
        questions: payload.questions,
        options: payload.options,
    };
}

/** Resolve clarifying questions from the approval payload or ask_user_question tool args. */
export function resolveApprovalQuestions(
    approval: ToolApprovalPayload | ToolApprovalActions | null | undefined,
    thoughts?: ThinkingEvent[],
): ClarifyingQuestion[] {
    if (approval?.questions?.length) {
        return approval.questions;
    }
    const decisionType = approval && 'decisionType' in approval ? approval.decisionType : approval?.decision_type;
    if (approval?.tool_name !== 'ask_user_question' && decisionType !== 'questions') return [];
    const toolEvt = thoughts?.slice().reverse().find(
        (e) => e.type === 'tool_start' && e.name === 'ask_user_question',
    );
    const raw = toolEvt?.params?.questions;
    if (!Array.isArray(raw)) return [];
    return raw.filter(
        (q): q is ClarifyingQuestion =>
            typeof q === 'object' &&
            q !== null &&
            typeof (q as ClarifyingQuestion).question === 'string' &&
            Array.isArray((q as ClarifyingQuestion).options),
    );
}

export function isQuestionsApproval(
    approval: ToolApprovalPayload | ToolApprovalActions | null | undefined,
    thoughts?: ThinkingEvent[],
): boolean {
    const decisionType =
        approval && 'decisionType' in approval
            ? approval.decisionType
            : approval?.decision_type;
    if (decisionType === 'questions') return true;
    return resolveApprovalQuestions(approval, thoughts).length > 0;
}
