import type { ThinkingEvent } from '../../store';
import type { ToolApprovalActions, ToolApprovalPayload } from '../../utils/tool-approval';
import { isQuestionsApproval, resolveApprovalQuestions } from '../../utils/tool-approval';
import ClarifyingQuestionsCard from './ClarifyingQuestionsCard';
import ApprovalCard from './ApprovalCard';

interface ToolApprovalPanelProps {
    approval: ToolApprovalPayload | ToolApprovalActions;
    thoughts?: ThinkingEvent[];
    onResolve: (decision: string, displayText?: string) => void;
}

/**
 * Renders the interactive approval UI for a paused tool (accept/decline,
 * multiple-choice, or structured clarifying questions).
 */
export default function ToolApprovalPanel({ approval, thoughts, onResolve }: ToolApprovalPanelProps) {
    const resolved = 'resolved' in approval ? approval.resolved : false;
    const userChoice = 'userChoice' in approval ? approval.userChoice : undefined;
    const prompt =
        'prompt' in approval ? approval.prompt : (approval as ToolApprovalPayload).prompt;
    const title = 'title' in approval ? approval.title : (approval as ToolApprovalPayload).title;

    if (isQuestionsApproval(approval, thoughts)) {
        const questions = resolveApprovalQuestions(approval, thoughts);
        if (questions.length === 0) return null;
        // Resolved Q&A renders on the USER side (two-sided conversation). Keep the
        // interactive card on Chad's side only while unanswered.
        if (resolved) return null;

        return (
            <ClarifyingQuestionsCard
                title={title}
                questions={questions}
                resolved={false}
                userChoice={userChoice}
                onResolve={(decision, displayText) => onResolve(decision, displayText)}
            />
        );
    }

    const decisionType =
        'decisionType' in approval
            ? approval.decisionType === 'questions'
                ? 'choice'
                : approval.decisionType
            : approval.decision_type === 'questions'
              ? 'choice'
              : approval.decision_type || 'accept_decline';

    const toolName =
        ('tool_name' in approval ? approval.tool_name : undefined) ||
        ('tool' in approval ? ((approval as unknown) as Record<string, unknown>).tool as string : undefined);

    const toolEvt = toolName ? thoughts?.slice().reverse().find(
        (e) => (e.type === 'tool_start' || e.type === 'tool_end') &&
               e.name === toolName && e.params
    ) : undefined;

    const resolvedToolName = toolName || toolEvt?.name;
    const params = approval.params || toolEvt?.params;
    const options = approval.options;

    return (
        <ApprovalCard
            prompt={prompt || 'Please confirm to continue.'}
            decisionType={decisionType}
            options={options}
            resolved={resolved}
            userChoice={userChoice}
            onResolve={(decision) => onResolve(decision)}
            toolName={resolvedToolName}
            params={params}
        />
    );
}
