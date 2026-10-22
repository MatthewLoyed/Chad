// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AutomationsTab from './AutomationsTab';

let enabled = true;
const summary = () => ({
    autonomous_actions_enabled: enabled, autonomous_actions_locked: false,
    heartbeat: { status: 'offline', interval_minutes: 30, last_check: null, checklist: '' },
    cron: { task_count: 0, total_count: 0 }, webhooks: {}, daemons: [], recent_runs: [],
});
beforeEach(() => { enabled = true; localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('pauses and resumes all autonomous actions through one switch', async () => {
    const update = vi.fn(async (_id, value) => { enabled = value; return { success: true }; });
    window.pywebview = { api: {
        get_automations_summary: vi.fn(async () => summary()),
        list_cron_jobs: vi.fn(async () => []), update_daemon_settings: update,
    } } as never;
    render(<AutomationsTab />);
    const toggle = await screen.findByRole('switch', { name: 'Autonomous actions' });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    fireEvent.click(toggle);
    await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(false));
    expect(update).toHaveBeenLastCalledWith('master', false, undefined);
    await act(async () => {});
    fireEvent.click(toggle);
    await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true));
    expect(update).toHaveBeenLastCalledWith('master', true, undefined);
});

it('keeps the prior state and shows a failed save', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    window.pywebview = { api: {
        get_automations_summary: vi.fn(async () => summary()),
        list_cron_jobs: vi.fn(async () => []),
        update_daemon_settings: vi.fn(async () => ({ success: false, error: 'Save failed' })),
    } } as never;
    render(<AutomationsTab />);
    const toggle = await screen.findByRole('switch', { name: 'Autonomous actions' });
    fireEvent.click(toggle);
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Save failed');
    expect((toggle as HTMLInputElement).checked).toBe(true);
});
