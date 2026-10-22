// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ChatStream from '../components/layout/ChatStream';
import { useAppStore } from '../store';
import { StreamEventEmitter } from '../transport/streamEmitter';
import { installStreamHandlers } from '../runtime/streamRuntime';

afterEach(cleanup);
it('replaces browser status in the real chat renderer without leaking reasoning', () => {
    Element.prototype.scrollIntoView = vi.fn();
    window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    useAppStore.setState({ chatMessages: [], activeConversationId: 'test', streamConversationId: 'test',
        streamTurn: null, activeTurn: null, status: 'thinking', apiReady: true, pendingToolApproval: null });
    const uninstall = installStreamHandlers();
    const emitter = new StreamEventEmitter('browser-turn');
    const progress = { run_id: 'browser-run', status: 'running' as const, milestone: 'Find the conversation',
        subgoal: 'Search chat titles', reason: '', actions: 1, recoveries: 0, parent_id: 'delegation-1' };
    act(() => {
        window.applyStreamEvent!(emitter.runStarted());
        window.applyStreamEvent!(emitter.toolStart('delegation-1', 'delegate_to_browser_agent',
            { task: 'Capture the requested response' }, 'Open requested chat'));
        window.applyStreamEvent!(emitter.browserProgress(progress));
    });
    render(<ChatStream />);
    expect(screen.getByRole('status').textContent).toContain('Search chat titles');
    act(() => window.applyStreamEvent!(emitter.browserProgress({ ...progress, status: 'completed',
        milestone: 'Full message captured', subgoal: '', actions: 4 })));
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(screen.getByRole('status').textContent).toContain('Full message captured');
    expect(screen.getByRole('status').textContent).not.toContain('Search chat titles');
    expect(useAppStore.getState().streamTurn?.parts.filter(p => p.kind === 'browser_progress')).toHaveLength(1);
    uninstall();
});
