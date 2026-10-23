// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FeedWorkspace from './FeedWorkspace';
import { useAppStore } from '../../store';
import { writeCache } from '../../utils/localCache';

const run = (id: string, silent = false) => ({
    id, source: 'cron', outcome: 'completed', summary: `Update ${id}`,
    tools: [], silent, duration_sec: 1, ran_at: `2026-10-23T10:0${id}:00Z`,
});
beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ activeWorkspace: 'feed', apiReady: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('shows newest updates first and keeps quiet checks opt-in', async () => {
    window.pywebview = { api: { get_automations_summary: vi.fn(async () => ({ recent_runs: [run('1'), run('3', true), run('2')] })) } } as never;
    const { container } = render(<FeedWorkspace />);
    await screen.findByText('Update 2');
    expect([...container.querySelectorAll('article p')].map((p) => p.textContent)).toEqual(['Update 2', 'Update 1']);
    expect(screen.queryByText('Update 3')).toBeNull();
    fireEvent.click(screen.getByRole('checkbox'));
    expect([...container.querySelectorAll('article p')].map((p) => p.textContent)).toEqual(['Update 3', 'Update 2', 'Update 1']);
});

it('keeps cached updates on failure and refresh recovers', async () => {
    writeCache('automations_summary', { recent_runs: [run('1')] });
    const getter = vi.fn().mockRejectedValueOnce(new Error('Connection lost')).mockResolvedValue({ recent_runs: [run('2')] });
    window.pywebview = { api: { get_automations_summary: getter } } as never;
    render(<FeedWorkspace />);
    await screen.findByText("Couldn't refresh activity");
    expect(screen.queryByText('Update 1')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Update 2');
    await waitFor(() => expect(screen.queryByText("Couldn't refresh activity")).toBeNull());
});

it('refreshes on opening Feed after API discovery, without fetching while hidden', async () => {
    const getter = vi.fn(async () => ({ recent_runs: [] }));
    window.pywebview = { api: { get_automations_summary: getter } } as never;
    useAppStore.setState({ activeWorkspace: 'chat' });
    render(<FeedWorkspace />);
    expect(getter).not.toHaveBeenCalled();
    useAppStore.setState({ activeWorkspace: 'feed' });
    await screen.findByText("You're caught up");
    expect(getter).toHaveBeenCalledTimes(1);
});
