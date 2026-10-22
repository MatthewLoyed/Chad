import { expect, it, vi } from 'vitest';
import { brainSocket } from './brainSocket';
import { StreamEventEmitter } from './streamEmitter';

it('a late paused-turn response cannot close the approval continuation stream', () => {
    const applyStreamEvent = vi.fn();
    const addAIMessageToChat = vi.fn();
    (globalThis as unknown as { window: unknown }).window = {
        applyStreamEvent, addAIMessageToChat, handleStateUpdate: vi.fn(),
    };
    const socket = brainSocket as unknown as {
        currentTurnId: string; pendingConversationId: string;
        emitter: StreamEventEmitter | null; handleResponse: (p: Record<string, unknown>) => void;
    };
    socket.currentTurnId = 'approval-continuation';
    socket.pendingConversationId = 'same-conversation';
    const emitter = new StreamEventEmitter('approval-continuation');
    socket.emitter = emitter;
    socket.handleResponse({ turn_id: 'paused-turn', conversation_id: 'same-conversation', text: 'Approve?' });
    expect(socket.emitter).toBe(emitter);
    expect(applyStreamEvent).not.toHaveBeenCalled();
    expect(addAIMessageToChat).not.toHaveBeenCalled();
    socket.handleResponse({ turn_id: 'approval-continuation', conversation_id: 'same-conversation', text: 'Done' });
    expect(addAIMessageToChat).toHaveBeenCalledOnce();
    expect(socket.emitter).toBeNull();
});
