// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import ApprovalCard from './ApprovalCard';

afterEach(cleanup);

describe('ApprovalCard', () => {
    it('renders email approval card with To, Subject, and action buttons', () => {
        const onResolve = vi.fn();
        render(
            <ApprovalCard
                prompt="Approve sending this email?"
                decisionType="accept_decline"
                toolName="send_email"
                params={{
                    to: 'alice@example.com',
                    subject: 'Weekly Status Report',
                    body: 'Here are the updates:\n- Item 1\n- Item 2',
                }}
                onResolve={onResolve}
            />
        );

        expect(screen.getByText('Send Email')).toBeDefined();
        expect(screen.getByText('alice@example.com')).toBeDefined();
        expect(screen.getByText('Weekly Status Report')).toBeDefined();
        expect(screen.getByText('Approve & Send Email')).toBeDefined();
        expect(screen.getByText('Decline')).toBeDefined();

        fireEvent.click(screen.getByText('Approve & Send Email'));
        expect(onResolve).toHaveBeenCalledWith('approved');
    });

    it('renders command execution card with code block and directory', () => {
        const onResolve = vi.fn();
        render(
            <ApprovalCard
                prompt="Execute shell command?"
                decisionType="accept_decline"
                toolName="run_command"
                params={{
                    command: 'npm run test:unit',
                    cwd: 'src/clients/web',
                }}
                onResolve={onResolve}
            />
        );

        expect(screen.getByText('Run Terminal Command')).toBeDefined();
        expect(screen.getByText('npm run test:unit')).toBeDefined();
        expect(screen.getByText('src/clients/web')).toBeDefined();
        expect(screen.getByText('Approve & Execute')).toBeDefined();

        fireEvent.click(screen.getByText('Decline'));
        expect(onResolve).toHaveBeenCalledWith('declined');
    });

    it('renders file change card with target file path', () => {
        render(
            <ApprovalCard
                prompt="Apply file modification?"
                decisionType="accept_decline"
                toolName="write_file"
                params={{
                    file_path: '/etc/nginx/nginx.conf',
                    content: 'server { listen 80; }',
                }}
            />
        );

        expect(screen.getByText('File Modification')).toBeDefined();
        expect(screen.getByText('/etc/nginx/nginx.conf')).toBeDefined();
        expect(screen.getByText('server { listen 80; }')).toBeDefined();
        expect(screen.getByText('Approve & Apply')).toBeDefined();
    });

    it('renders calendar event card with event title and time', () => {
        render(
            <ApprovalCard
                prompt="Schedule event?"
                decisionType="accept_decline"
                toolName="add_calendar_event"
                params={{
                    title: 'Team Sync',
                    when: 'Tomorrow at 10:00 AM',
                    attendees: ['alice@example.com', 'bob@example.com'],
                }}
            />
        );

        expect(screen.getByText('Calendar Event')).toBeDefined();
        expect(screen.getByText('Team Sync')).toBeDefined();
        expect(screen.getByText('Tomorrow at 10:00 AM')).toBeDefined();
        expect(screen.getByText('alice@example.com, bob@example.com')).toBeDefined();
        expect(screen.getByText('Approve & Schedule')).toBeDefined();
    });

    it('renders resolved status chip and disables actions when resolved', () => {
        render(
            <ApprovalCard
                prompt="Approved action"
                decisionType="accept_decline"
                toolName="send_email"
                params={{ to: 'alice@example.com', subject: 'Hi' }}
                resolved={true}
                userChoice="accepted"
            />
        );

        expect(screen.getByText('Sent')).toBeDefined();
        expect(screen.getByText('Email approved and sent')).toBeDefined();
        expect(screen.queryByText('Approve & Send Email')).toBeNull();
    });
});
