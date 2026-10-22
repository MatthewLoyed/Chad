// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import ToolApprovalPanel from './ToolApprovalPanel';
import { buildToolApprovalActions } from '../../utils/tool-approval';
import type { ThinkingEvent } from '../../store';

afterEach(cleanup);
const unrelated: ThinkingEvent[] = [
    { type: 'tool_start', name: 'open_app', params: { name: 'Prism Launcher' } },
];

describe('approval action identity', () => {
    it('retains the requested tool through persistence and ignores unrelated tool args', () => {
        const approval = buildToolApprovalActions({
            token: 'approval-test', tool_name: 'create_gmail_draft',
            prompt: 'Allow create_gmail_draft to proceed?',
        });
        render(<ToolApprovalPanel approval={approval} thoughts={unrelated} onResolve={vi.fn()} />);
        expect(screen.getByText('Create Email Draft')).toBeDefined();
        expect(screen.queryByText('Prism Launcher')).toBeNull();
        expect(screen.queryByText('Approve & Send Email')).toBeNull();
    });

    it('shows approval payload params, never a different action preview', () => {
        const approval = buildToolApprovalActions({
            token: 'approval-send', tool_name: 'send_email', prompt: 'Send this email?',
            params: { to: 'person@example.com', subject: 'Meeting link', body: 'Here is the link.' },
        });
        render(<ToolApprovalPanel approval={approval} thoughts={unrelated} onResolve={vi.fn()} />);
        expect(screen.getByText('person@example.com')).toBeDefined();
        expect(screen.getByText('Meeting link')).toBeDefined();
        expect(screen.getByText('Approve & Send Email')).toBeDefined();
        expect(screen.queryByText('Prism Launcher')).toBeNull();
    });

    it('renders legacy approvals generically instead of borrowing an unrelated tool', () => {
        render(<ToolApprovalPanel approval={{ token: 'legacy', prompt: 'Confirm?' }} thoughts={unrelated} onResolve={vi.fn()} />);
        expect(screen.getByText('Action Approval')).toBeDefined();
        expect(screen.queryByText('Prism Launcher')).toBeNull();
    });

    it('does not replace a send approval with questions from an earlier tool', () => {
        const thoughts: ThinkingEvent[] = [{type: 'tool_start', name: 'ask_user_question', params: {
            questions: [{question: 'Unrelated question?', options: [{label: 'A'}]}],
        }}];
        render(<ToolApprovalPanel approval={{token: 'send', tool_name: 'send_email', prompt: 'Send?'}} thoughts={thoughts} onResolve={vi.fn()} />);
        expect(screen.getByText('Send Email')).toBeDefined();
        expect(screen.queryByText('Unrelated question?')).toBeNull();
    });
});
