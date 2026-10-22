// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ChatStream from '../components/layout/ChatStream';
import { getActiveChatStreamTurn, useAppStore } from '../store';
import { installStreamHandlers } from '../runtime/streamRuntime';
import { StreamEventEmitter } from '../transport/streamEmitter';

let uninstall: () => void;
beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    useAppStore.setState({
        chatMessages: [{
            id: 'plan-turn', role: 'ai', text: '', timestamp: new Date(),
            stop_reason: 'awaiting_approval',
            thoughts: [{ type: 'execution_plan', plan_id: 'plan', plan: [
                { id: 'step', description: 'Find the requested information', status: 'pending' },
            ] }],
        }],
        activeConversationId: 'plan-chat', streamConversationId: null,
        streamTurn: null, activeTurn: null, status: 'ready', apiReady: true,
        liveStatus: null, thinkingStartTime: null, thinkingEndTime: null,
        pendingToolApproval: null, skipNextAiTurnFinalizeId: null,
    });
    uninstall = installStreamHandlers();
});
afterEach(() => { cleanup(); uninstall(); vi.restoreAllMocks(); });

it.each(['Proceed', 'Cancel'])('%s keeps desktop continuation thoughts and actions visible', async (label) => {
    // Desktop emits typed events but, unlike brainSocket.sendInput, does not
    // assign frontend streamConversationId. Exercise the real click and sink.
    const send = vi.fn().mockResolvedValue(true);
    window.pywebview = { api: { send_text_command: send } } as never;
    const { container } = render(<ChatStream />);
    fireEvent.click(screen.getByRole('button', { name: /Execution Plan/ }));
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(useAppStore.getState().status).toBe('thinking');
    expect(useAppStore.getState().thinkingEndTime).toBeNull();
    await act(async () => {});
    expect(send).toHaveBeenCalledWith(label === 'Proceed' ? 'go ahead' : 'cancel plan', null, null, 'plan-chat', null, null);

    const emitter = new StreamEventEmitter('continuation');
    act(() => {
        window.applyStreamEvent!(emitter.runStarted());
        window.applyStreamEvent!(emitter.reasoningDelta('Checking the approved request'));
        window.applyStreamEvent!([{
            type: 'tool_start', turn_id: 'continuation', seq: 2,
            tool_call_id: 'search', name: 'web_search', args: { query: 'test' },
            display_text: 'Searching for the requested information',
        }]);
    });
    expect(container.textContent).toContain('Checking the approved request');
    expect(container.textContent).toContain('Searching for the requested information');
    expect(useAppStore.getState().streamConversationId).toBe('plan-chat');

    act(() => useAppStore.getState().setActiveConversation('other-chat'));
    // AnimatePresence keeps exiting nodes in jsdom, so assert the scoped
    // selector directly while the stream is backgrounded.
    expect(getActiveChatStreamTurn(useAppStore.getState())).toBeNull();
    act(() => useAppStore.getState().setActiveConversation('plan-chat'));
    expect(getActiveChatStreamTurn(useAppStore.getState())?.turn_id).toBe('continuation');
    expect(container.textContent).toContain('Checking the approved request');
    expect(container.textContent).toContain('Searching for the requested information');
});

it.each(['false', 'rejected'])('clears optimistic busy state when the bridge returns %s', async (failure) => {
    const send = failure === 'false'
        ? vi.fn().mockResolvedValue(false)
        : vi.fn().mockRejectedValue(new Error('Disconnected'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    window.pywebview = { api: { send_text_command: send } } as never;
    render(<ChatStream />);
    fireEvent.click(screen.getByRole('button', { name: /Execution Plan/ }));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Proceed' })));
    expect(useAppStore.getState().status).toBe('ready');
    expect(useAppStore.getState().streamConversationId).toBeNull();
    expect(useAppStore.getState().chatMessages.at(-1)?.sendStatus).toBe('failed');
});
