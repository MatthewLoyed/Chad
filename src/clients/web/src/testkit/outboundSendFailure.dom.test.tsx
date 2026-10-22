// @vitest-environment jsdom
/**
 * Soft-failure send path: optimistic USER bubbles must not leave the app stuck
 * busy when handoff to the Brain fails. Chat-standard pattern — keep the bubble,
 * mark failed, offer Retry, clear thinking/wait theater.
 */
import { afterEach, describe, expect, it } from 'vitest';
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

const userFailed = (id: string, text: string): ChatMessage => ({
    id,
    role: 'user',
    text,
    timestamp: new Date(2026, 6, 28, 12, 0),
    sendStatus: 'failed',
});

describe('outbound send soft-failure', () => {
    it('failOutboundSend marks the bubble failed and clears busy wait state', () => {
        useAppStore.setState({
            chatMessages: [{
                id: 'u1',
                role: 'user',
                text: 'hello',
                timestamp: new Date(),
                sendStatus: 'sending',
            }],
            status: 'thinking',
            statusText: 'Processing Prompt...',
            liveStatus: 'Got it — working on this…',
            liveStatusUpdatedAt: Date.now(),
            thinkingStartTime: Date.now(),
            streamConversationId: REPLAY_CONVERSATION_ID,
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
        });

        useAppStore.getState().failOutboundSend('u1');
        const s = useAppStore.getState();
        expect(s.chatMessages[0].sendStatus).toBe('failed');
        expect(s.status).toBe('ready');
        expect(s.statusText).toBe('');
        expect(s.liveStatus).toBeNull();
        expect(s.streamConversationId).toBeNull();
    });

    it('renders Couldn\'t send + Retry and invokes onRetryFailedSend', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: [userFailed('u-fail', 'ping the board')],
            status: 'ready',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: null,
            apiReady: true,
        });

        const retries: Array<{ id: string; text: string }> = [];
        const { container, getByRole } = render(
            <ChatStream onRetryFailedSend={(id, text) => retries.push({ id, text })} />,
        );

        expect(container.textContent || '').toMatch(/Couldn.?t send/i);
        fireEvent.click(getByRole('button', { name: /retry/i }));
        expect(retries).toEqual([{ id: 'u-fail', text: 'ping the board' }]);
    });

    it('shows Sending… while sendStatus is sending and the API is ready', () => {
        installShims();
        useAppStore.getState().clearThinkingEvents();
        useAppStore.setState({
            chatMessages: [{
                id: 'u2',
                role: 'user',
                text: 'hi',
                timestamp: new Date(),
                sendStatus: 'sending',
            }],
            status: 'thinking',
            streamTurn: null,
            activeConversationId: REPLAY_CONVERSATION_ID,
            streamConversationId: REPLAY_CONVERSATION_ID,
            apiReady: true,
        });

        const { container } = render(<ChatStream />);
        expect(container.textContent || '').toMatch(/Sending/i);
    });
});
