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

it('shows sourced discoveries and keeps failed dismissals visible', async () => {
    const entry = { id: 'find-1', kind: 'discovery', title: 'Local models in practice', body: 'A small experiment.', created_at: '2026-10-23T10:00:00Z', status: 'active', metadata: { url: 'https://example.com/article' } };
    const dismiss = vi.fn().mockResolvedValueOnce({ success: false }).mockResolvedValueOnce({ success: true });
    window.pywebview = { api: { get_dashboard_feed: vi.fn(async () => [entry]), get_automations_summary: vi.fn(async () => ({ recent_runs: [] })), dismiss_dashboard_entry: dismiss } } as never;
    render(<FeedWorkspace />);
    await screen.findByText(entry.title);
    expect(screen.getByRole('link', { name: 'Read original' }).getAttribute('href')).toBe(entry.metadata.url);
    fireEvent.click(screen.getByRole('button', { name: `Dismiss ${entry.title}` }));
    await screen.findByRole('alert');
    expect(screen.queryByText(entry.title)).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: `Dismiss ${entry.title}` }));
    await waitFor(() => expect(screen.queryByText(entry.title)).toBeNull());
});

it('rejects unsafe source URLs and carries a read into chat without sending', async () => {
    const entry = { id: 'safe-ui', kind: 'discovery', title: 'A sourced read', body: 'Useful context.', status: 'active', created_at: '2026-10-24T12:00:00Z', metadata: { url: 'javascript:alert(1)', content_type: 'source_summary' } };
    window.pywebview = { api: { get_dashboard_feed: vi.fn(async () => [entry]), get_automations_summary: vi.fn(async () => ({ recent_runs: [] })) } } as never;
    const compose = vi.fn();
    window.addEventListener('chad:compose-feed', compose);
    try {
        render(<FeedWorkspace />);
        await screen.findByText(entry.title);
        expect(screen.queryByRole('link', { name: 'Read original' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Discuss' }));
        expect(useAppStore.getState().activeWorkspace).toBe('chat');
        expect((compose.mock.calls[0][0] as CustomEvent).detail).toContain(entry.body);
        expect((compose.mock.calls[0][0] as CustomEvent).detail).not.toContain('javascript:');
    } finally { window.removeEventListener('chad:compose-feed', compose); }
});

it('shows a source image and reveals the saved selection reason on demand', async () => {
    const entry = { id: 'image-post', kind: 'discovery', title: 'A local notes tool', body: 'It searches notes locally. It uses keyword matching.', status: 'active', created_at: '2026-10-24T12:00:00Z', metadata: { url: 'https://example.com/notes', image_url: 'https://example.com/notes.png', selection_reason: 'Matches Python in your interest profile.' } };
    window.pywebview = { api: { get_dashboard_feed: vi.fn(async () => [entry]), get_automations_summary: vi.fn(async () => ({ recent_runs: [] })) } } as never;
    render(<FeedWorkspace />);
    await screen.findByText(entry.title);
    expect(screen.queryByText(/Matches Python/)).toBeNull();
    const info = screen.getByRole('button', { name: `Why Chad picked ${entry.title}` });
    fireEvent.click(info);
    expect(info.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/Matches Python/)).not.toBeNull();
    const imageLink = screen.getByRole('link', { name: `View image for ${entry.title}` });
    const image = imageLink.querySelector('img')!;
    expect(image.getAttribute('src')).toBe(entry.metadata.image_url);
    expect(image.getAttribute('referrerpolicy')).toBe('no-referrer');
    fireEvent.error(image);
    expect(screen.queryByRole('link', { name: `View image for ${entry.title}` })).toBeNull();
    expect(screen.getByText(entry.title)).not.toBeNull();
});
