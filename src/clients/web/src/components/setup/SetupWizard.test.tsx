// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SetupWizard from './SetupWizard';
import bundleFixture from '../../stories/fixtures/setupBundle.json';
afterEach(() => { cleanup(); delete window.pywebview; });
function enterLmStudio() {
    if (window.pywebview?.api) window.pywebview.api.test_setup_model = vi.fn().mockResolvedValue({ success: true, reply: 'Hello!' });
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /LM Studio Recommended/ }));
    fireEvent.click(screen.getByRole('button', { name: 'LM Studio is open — continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'I already have a model — continue' }));
}
it('explains an installed app with no server and lets the user go back or finish later', async () => {
    window.pywebview = { api: {
        probe_local_backend: vi.fn().mockResolvedValue({ reachable: false, models: [] }),
        get_setup_status: vi.fn().mockResolvedValue({ needs_setup: true, lmstudio_installed: true, server_running: false }),
    } };
    render(<SetupWizard onComplete={vi.fn()} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: /Check connection/ }));
    expect(await screen.findByText(/LM Studio was found/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('heading', { name: 'Find your first AI model' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Finish later' }));
    expect(screen.getByText(/Chat is not ready yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Continue setup' }));
    expect(screen.getByRole('heading', { name: 'Where should your AI run?' })).toBeTruthy();
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
    fireEvent.click(screen.getByRole('button', { name: /Check connection/ }));
    await screen.findByRole('option', { name: 'Fictional Chat' });
    expect(getStatus).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Use this model/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Send a short test message' }));
    await screen.findByText('Your model answered');
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
    fireEvent.click(screen.getByRole('button', { name: /Check connection/ }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Connection check failed');
    fireEvent.click(screen.getByRole('button', { name: /Check connection/ }));
    await screen.findByRole('option', { name: 'Test model' });
    expect(screen.queryByRole('alert')).toBeNull();
});
it('discards a late reply after leaving the model page', async () => {
    let resolve!: (value: unknown) => void;
    window.pywebview = { api: { probe_local_backend: vi.fn().mockReturnValue(new Promise(r => { resolve = r; })) } };
    render(<SetupWizard onComplete={vi.fn()} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: /Check connection/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    resolve({ reachable: true, models: [{ id: 'late', display_name: 'Late model' }] });
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Find your first AI model' })).toBeTruthy());
    expect(screen.queryByText('Late model')).toBeNull();
});
it('shows a finite path and keeps connection fields out of the beginner model step', () => {
    render(<SetupWizard onComplete={vi.fn()} />);
    expect(screen.getByRole('navigation', { name: 'Setup progress' }).querySelector('[aria-current="step"]')?.textContent).toContain('Welcome');
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /LM Studio Recommended/ }));
    fireEvent.click(screen.getByRole('button', { name: 'LM Studio is open — continue' }));
    expect(screen.getByText('Step 3 of 4')).toBeTruthy();
    expect(screen.queryByLabelText('Server address')).toBeNull();
    expect(screen.getByRole('button', { name: 'Find a model for me' })).toBeTruthy();
    expect(screen.getByLabelText('Context tokens').closest('details')?.open).toBe(false);
});

it('lets users retry when the server is reachable but no model is available', async () => {
    const probe = vi.fn().mockResolvedValueOnce({ reachable: true, models: [] })
        .mockResolvedValue({ reachable: true, models: [{ id: 'ready', display_name: 'Ready Chat' }] });
    window.pywebview = { api: { probe_local_backend: probe,
        get_setup_status: vi.fn().mockResolvedValue({ server_running: true }) } };
    render(<SetupWizard onComplete={vi.fn()} />);
    enterLmStudio();
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByText(/default LM Studio server is running/);
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByRole('option', { name: 'Ready Chat' });
});

it('offers cloud setup without local installs and tests a manually entered model without discovery', async () => {
    const test = vi.fn().mockResolvedValue({ success: true, reply: 'Hello from the test provider.' });
    const save = vi.fn().mockResolvedValue({ success: true });
    const probe = vi.fn();
    window.pywebview = { api: { test_setup_model: test, save_provider_choice: save, probe_local_backend: probe } };
    render(<SetupWizard onComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /Use a cloud API/ }));
    expect(screen.queryByRole('button', { name: 'Find a model for me' })).toBeNull();
    fireEvent.change(screen.getByLabelText('Provider API key'), { target: { value: 'fictional-key' } });
    fireEvent.change(screen.getByLabelText('Model ID (from your model app or provider)'), { target: { value: 'vendor/chat' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send a short test message' }));
    await screen.findByText('Your model answered');
    expect(test).toHaveBeenCalledWith('https://openrouter.ai/api/v1', 'fictional-key', 'vendor/chat', true);
    expect(probe).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Model ID (from your model app or provider)'), { target: { value: 'vendor/other' } });
    expect(screen.queryByRole('button', { name: /Use this model/ })).toBeNull();
    expect(save).not.toHaveBeenCalled();
});

it('does not allow a failed chat test to complete setup', async () => {
    window.pywebview = { api: { test_setup_model: vi.fn().mockResolvedValue({ success: false, detail: 'Provider credit unavailable.' }) } };
    render(<SetupWizard onComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /Use a cloud API/ }));
    fireEvent.change(screen.getByLabelText('Model ID (from your model app or provider)'), { target: { value: 'vendor/chat' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send a short test message' }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Provider credit unavailable.');
    expect(screen.queryByRole('button', { name: /Use this model/ })).toBeNull();
});

it('offers explicit Chat-only fallback after a failed helper check and preserves its proof', async () => {
    const save = vi.fn().mockResolvedValue({ success: true });
    window.pywebview = { api: {
        assess_model_hardware: vi.fn().mockResolvedValue(bundleFixture),
        probe_local_backend: vi.fn().mockResolvedValue({ reachable: true, bundle_models: { 'bonsai-27b-q1': 'existing-bonsai' }, models: [{ id: 'existing-bonsai', display_name: 'Bonsai' }] }),
        test_setup_model: vi.fn(),
        test_setup_bundle: vi.fn().mockResolvedValue({ success: false, reply: 'Hello', detail: 'Helper unavailable.', validation_token: 'fictional-proof' }),
        save_setup_bundle: save, save_provider_choice: vi.fn(),
    } };
    render(<SetupWizard onComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /LM Studio Recommended/ }));
    fireEvent.click(screen.getByRole('button', { name: 'LM Studio is open — continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    await screen.findByRole('heading', { name: 'Bonsai-27B' });
    fireEvent.click(screen.getByRole('button', { name: 'I already have a model — continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByRole('option', { name: 'Bonsai' });
    fireEvent.click(screen.getByRole('button', { name: 'Test recommended setup' }));
    await screen.findByText('Helper unavailable.');
    expect(screen.queryByRole('button', { name: 'Use this model and open Chat' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Chat only' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('http://localhost:1234/v1', '', 'existing-bonsai', 'fictional-proof', true));
    expect(window.pywebview?.api?.save_provider_choice).not.toHaveBeenCalled();
});
