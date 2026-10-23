// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import SuggestedActions from './SuggestedActions';
import { useAppStore } from '../../store';

afterEach(() => { cleanup(); useAppStore.setState({ chatTurnMode: 'auto' }); });

it('starter suggestions follow Chat mode and a later switch to Plan mode', () => {
    useAppStore.setState({ chatTurnMode: 'chat' });
    const send = vi.fn();
    const { rerender } = render(<SuggestedActions onSelect={send} />);
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(send).toHaveBeenLastCalledWith(expect.any(String), 'chat');

    useAppStore.setState({ chatTurnMode: 'plan' });
    rerender(<SuggestedActions onSelect={send} />);
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(send).toHaveBeenLastCalledWith(expect.any(String), 'planning');
});
