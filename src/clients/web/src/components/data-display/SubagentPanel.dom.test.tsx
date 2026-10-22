// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import SubagentPanel from './SubagentPanel';

afterEach(cleanup);

describe('SubagentPanel', () => {
    it('renders agent name, status, and prompt preview in header when collapsed', () => {
        render(
            <SubagentPanel
                agentName="Research Agent"
                prompt="Find top 5 restaurants in Detroit"
                status="pending"
            >
                <div>Child reasoning</div>
            </SubagentPanel>
        );

        expect(screen.getByText('Research Agent')).toBeDefined();
        expect(screen.getByText('Working…')).toBeDefined();
        expect(screen.getByText('Find top 5 restaurants in Detroit')).toBeDefined();
        // Body is collapsed by default
        expect(screen.queryByTestId('subagent-prompt-card')).toBeNull();
    });

    it('expands on click and shows the supervisor prompt card', () => {
        render(
            <SubagentPanel
                agentName="Code Agent"
                prompt="Create an interactive solar system simulation"
                status="success"
            >
                <div>Done generating files</div>
            </SubagentPanel>
        );

        const header = screen.getByRole('button');
        fireEvent.click(header);

        const promptCard = screen.getByTestId('subagent-prompt-card');
        expect(promptCard).toBeDefined();
        expect(within(promptCard).getByText('Prompt')).toBeDefined();
        expect(within(promptCard).getByText('Create an interactive solar system simulation')).toBeDefined();
        expect(screen.getByText('Done generating files')).toBeDefined();
    });

    it('copies the prompt when copy button is clicked', async () => {
        const writeTextMock = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, {
            clipboard: { writeText: writeTextMock },
        });

        render(
            <SubagentPanel
                agentName="Code Agent"
                prompt="Write a Python script"
                status="success"
                defaultExpanded={true}
            />
        );

        const copyBtn = screen.getByRole('button', { name: 'Copy prompt' });
        fireEvent.click(copyBtn);

        expect(writeTextMock).toHaveBeenCalledWith('Write a Python script');
        await waitFor(() => {
            expect(screen.getByText('Copied')).toBeDefined();
        });
    });

    it('handles long prompts with show full prompt toggle', () => {
        const longPrompt = 'Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\nLine 7\nLine 8';
        render(
            <SubagentPanel
                agentName="Deep Research"
                prompt={longPrompt}
                status="success"
                defaultExpanded={true}
            />
        );

        const toggleBtn = screen.getByRole('button', { name: 'Show full prompt' });
        expect(toggleBtn).toBeDefined();

        fireEvent.click(toggleBtn);
        expect(screen.getByRole('button', { name: 'Show less' })).toBeDefined();
    });

    it('is expandable when prompt is present even without child elements', () => {
        render(
            <SubagentPanel
                agentName="Browser Agent"
                prompt="Navigate to sign-in page"
                status="pending"
            />
        );

        const header = screen.getByRole('button');
        fireEvent.click(header);
        expect(screen.getByTestId('subagent-prompt-card')).toBeDefined();
    });
});
