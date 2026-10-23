// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SetupWizard from './SetupWizard';

afterEach(() => { cleanup(); delete window.pywebview; });
function enterLmStudio() {
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /LM Studio Recommended/ }));
}
it('explains an installed app with no server and lets the user go back or finish later', async () => {
    window.pywebview = { api: {
        probe_local_backend: vi.fn().mockResolvedValue({ reachable: false, models: [] }),
        get_setup_status: vi.fn().mockResolvedValue({ needs_setup: true, lmstudio_installed: true, server_running: false }),
    } };
    render(<SetupWizard onComplete={vi.fn()} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: /check connection/ }));
    expect(await screen.findByText(/LM Studio was found/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('heading', { name: 'Choose your model app' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Finish later' }));
    expect(screen.getByText(/Chat is not ready yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Continue setup' }));
    expect(screen.getByRole('heading', { name: 'Choose your model app' })).toBeTruthy();
});
it('connects without CLI detection, saves a single chosen Chat model and opens Chat', async () => {
    const getStatus = vi.fn();
    const save = vi.fn().mockResolvedValue({ success: true });
    const complete = vi.fn();
    window.pywebview = { api: {
        get_setup_status: getStatus,
        probe_local_backend: vi.fn().mockResolvedValue({ reachable: true, models: [{ id: 'fictional-chat', display_name: 'Fictional Chat' }] }),
        save_provider_choice: save,
    } };
    render(<SetupWizard onComplete={complete} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: /check connection/ }));
    await screen.findByRole('option', { name: 'Fictional Chat' });
    expect(getStatus).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Use this model/ }));
    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(save).toHaveBeenCalledWith('lmstudio', 'http://localhost:1234/v1', '', 'fictional-chat', false);
});
it('shows request failures and permits a successful retry', async () => {
    const probe = vi.fn().mockRejectedValueOnce(new Error('Connection check failed'))
        .mockResolvedValue({ reachable: true, models: [{ id: 'test', display_name: 'Test model' }] });
    window.pywebview = { api: { probe_local_backend: probe } };
    render(<SetupWizard onComplete={vi.fn()} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: /check connection/ }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Connection check failed');
    fireEvent.click(screen.getByRole('button', { name: /check connection/ }));
    await screen.findByRole('option', { name: 'Test model' });
    expect(screen.queryByRole('alert')).toBeNull();
});
it('discards a late reply after leaving the model page', async () => {
    let resolve!: (value: unknown) => void;
    window.pywebview = { api: { probe_local_backend: vi.fn().mockReturnValue(new Promise(r => { resolve = r; })) } };
    render(<SetupWizard onComplete={vi.fn()} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: /check connection/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    resolve({ reachable: true, models: [{ id: 'late', display_name: 'Late model' }] });
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Choose your model app' })).toBeTruthy());
    expect(screen.queryByRole('option')).toBeNull();
});
