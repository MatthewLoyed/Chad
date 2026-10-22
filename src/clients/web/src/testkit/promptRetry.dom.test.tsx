// @vitest-environment jsdom
/**
 * Prompt retry & edit unit tests:
 * Verifies Anthropic & ChatGPT style retry on prompts, regeneration on AI responses,
 * and inline prompt editing in ChatStream.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import ChatStream from '../components/layout/ChatStream';
import { useAppStore, type ChatMessage } from '../store';
import { REPLAY_CONVERSATION_ID } from './domReplay';

afterEach(cleanup);

function installShims(): void {
    if (!Element.prototype.scrollIntoView) {
        Element.prototype.scrollIntoView = function () { /* no-op */ };
    }
    if (typeof window.matchMedia !== 'function') {
        window.matchMedia = ((q: string) => ({
            matches: false, media: q, onchange: null,
            addEventListener: () => {}, removeEventListener: () => {},
            addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
        })) as unknown as typeof window.matchMedia;
    }
    if (typeof window.ResizeObserver !== 'function') {
        window.ResizeObserver = class {
            observe() {} unobserve() {} disconnect() {}
        } as unknown as typeof window.ResizeObserver;
    }
}

const mockMessages: ChatMessage[] = [
    {
        id: 'msg-u1',
        role: 'user',
        text: 'What is the speed of light?',
        timestamp: new Date(2026, 9, 15, 10, 0),
    },
    {
        id: 'msg-a1',
        role: 'ai',
        text: 'The speed of light in vacuum is approximately 299,792,458 meters per second.',
        timestamp: new Date(2026, 9, 15, 10, 1),
    },
];

describe('Prompt Retry and Regenerate', () => {
    it('renders retry button on user prompt and invokes onRetryPrompt when clicked', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: mockMessages,
            status: 'ready',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: null,
            apiReady: true,
        });

        const onRetryPrompt = vi.fn();
        const { getByRole } = render(<ChatStream onRetryPrompt={onRetryPrompt} />);

        const retryPromptBtn = getByRole('button', { name: /retry prompt/i });
        expect(retryPromptBtn).toBeDefined();
        expect(retryPromptBtn.getAttribute('disabled')).toBeNull();

        fireEvent.click(retryPromptBtn);
        expect(onRetryPrompt).toHaveBeenCalledTimes(1);
        expect(onRetryPrompt).toHaveBeenCalledWith('msg-u1', 'What is the speed of light?');
    });

    it('renders regenerate button on AI response and retries the preceding user prompt', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: mockMessages,
            status: 'ready',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: null,
            apiReady: true,
        });

        const onRetryPrompt = vi.fn();
        const { getByRole } = render(<ChatStream onRetryPrompt={onRetryPrompt} />);

        const regenBtn = getByRole('button', { name: /regenerate response/i });
        expect(regenBtn).toBeDefined();

        fireEvent.click(regenBtn);
        expect(onRetryPrompt).toHaveBeenCalledTimes(1);
        expect(onRetryPrompt).toHaveBeenCalledWith('msg-u1', 'What is the speed of light?');
    });

    it('supports inline prompt editing with Save & Submit and Cancel', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: mockMessages,
            status: 'ready',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: null,
            apiReady: true,
        });

        const onRetryPrompt = vi.fn();
        const { getByRole, queryByRole, getByLabelText } = render(
            <ChatStream onRetryPrompt={onRetryPrompt} />
        );

        // Click Edit message
        const editBtn = getByRole('button', { name: /edit message/i });
        fireEvent.click(editBtn);

        // Textarea should now be visible
        const textarea = getByLabelText(/edit prompt/i) as HTMLTextAreaElement;
        expect(textarea).toBeDefined();
        expect(textarea.value).toBe('What is the speed of light?');

        // Modify text and submit
        fireEvent.change(textarea, { target: { value: 'What is the speed of sound?' } });
        const saveBtn = getByRole('button', { name: /save & submit/i });
        fireEvent.click(saveBtn);

        expect(onRetryPrompt).toHaveBeenCalledWith('msg-u1', 'What is the speed of sound?');
        // Editor should be closed
        expect(queryByRole('button', { name: /save & submit/i })).toBeNull();
    });

    it('cancels inline editing when Cancel is clicked', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: mockMessages,
            status: 'ready',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: null,
            apiReady: true,
        });

        const onRetryPrompt = vi.fn();
        const { getByRole, queryByRole } = render(<ChatStream onRetryPrompt={onRetryPrompt} />);

        fireEvent.click(getByRole('button', { name: /edit message/i }));
        expect(getByRole('button', { name: /cancel/i })).toBeDefined();

        fireEvent.click(getByRole('button', { name: /cancel/i }));
        expect(queryByRole('button', { name: /cancel/i })).toBeNull();
        expect(onRetryPrompt).not.toHaveBeenCalled();
    });

    it('disables retry and edit buttons when status is thinking (busy)', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: mockMessages,
            status: 'thinking',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: REPLAY_CONVERSATION_ID,
            apiReady: true,
        });

        const onRetryPrompt = vi.fn();
        const { getByRole } = render(<ChatStream onRetryPrompt={onRetryPrompt} />);

        const retryPromptBtn = getByRole('button', { name: /retry prompt/i }) as HTMLButtonElement;
        const editBtn = getByRole('button', { name: /edit message/i }) as HTMLButtonElement;
        const regenBtn = getByRole('button', { name: /regenerate response/i }) as HTMLButtonElement;

        expect(retryPromptBtn.disabled).toBe(true);
        expect(editBtn.disabled).toBe(true);
        expect(regenBtn.disabled).toBe(true);
    });
});
