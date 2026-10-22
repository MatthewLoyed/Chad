// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import TokenUsageTicker from './TokenUsageTicker';
import { useAppStore } from '../../store';

afterEach(cleanup);

beforeEach(() => {
    useAppStore.setState({
        chatMessages: [],
        tokenUsage: null,
    });
});

describe('TokenUsageTicker visibility and rendering', () => {
    it('is completely hidden when no messages exist on initial startup', () => {
        const { container } = render(<TokenUsageTicker />);
        expect(container.firstChild).toBeNull();
    });

    it('remains hidden even if messages exist when tokenUsage is null', () => {
        useAppStore.setState({
            chatMessages: [{ id: '1', role: 'user', text: 'Hello', timestamp: new Date() }],
            tokenUsage: null,
        });

        const { container } = render(<TokenUsageTicker />);
        expect(container.firstChild).toBeNull();
    });

    it('remains hidden if tokenUsage has only 0 or empty counts', () => {
        useAppStore.setState({
            chatMessages: [{ id: '1', role: 'user', text: 'Hello', timestamp: new Date() }],
            tokenUsage: {
                prompt_tokens: 0,
                completion_tokens: 0,
                total_tokens: 0,
            },
        });

        const { container } = render(<TokenUsageTicker />);
        expect(container.firstChild).toBeNull();
    });

    it('renders populated token stats once a message exists and tokens are > 0', () => {
        useAppStore.setState({
            chatMessages: [
                { id: '1', role: 'user', text: 'Hello', timestamp: new Date() },
                { id: '2', role: 'ai', text: 'World', timestamp: new Date() },
            ],
            tokenUsage: {
                prompt_tokens: 1500,
                completion_tokens: 250,
                total_tokens: 1750,
            },
        });

        const { container } = render(<TokenUsageTicker />);
        expect(container.querySelector('.token-usage-ticker')).not.toBeNull();
        expect(screen.getByText('Prompt:')).toBeDefined();
        expect(screen.getByText('1.5k')).toBeDefined();
        expect(screen.getByText('Resp:')).toBeDefined();
        expect(screen.getByText('250')).toBeDefined();
        expect(screen.getByText('Total:')).toBeDefined();
        expect(screen.getByText('1.8k')).toBeDefined();
    });
});
