// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SetupWizard from './SetupWizard';

afterEach(() => { cleanup(); delete window.pywebview; });

it('detects both servers and reaches an installed model without URL entry', async () => {
    const test = vi.fn().mockResolvedValue({ success: true, reply: 'Hello' });
    const save = vi.fn().mockResolvedValue({ success: true });
    const complete = vi.fn();
    window.pywebview = { api: {
        discover_setup_servers: vi.fn().mockResolvedValue({ scope: 'brain_host', servers: [
            { provider: 'ollama', label: 'Ollama', base_url: 'http://localhost:11434/v1', reachable: true, models: [{ id: 'fictional:9b', display_name: 'Fictional Chat' }] },
            { provider: 'lmstudio', label: 'LM Studio', base_url: 'http://localhost:1234/v1', reachable: true, models: [] },
        ] }), test_setup_model: test, save_provider_choice: save,
    } };
    render(<SetupWizard onComplete={complete} />);
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(await screen.findByRole('button', { name: /Ollama found/ }));
    expect(screen.getByRole('option', { name: 'Fictional Chat' })).toBeTruthy();
    expect(test).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Send a short test message' }));
    await screen.findByText('Your model answered');
    fireEvent.click(screen.getByRole('button', { name: 'Use this model and open Chat' }));
    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(save).toHaveBeenCalledWith('ollama', 'http://localhost:11434/v1', '', 'fictional:9b', false);
});

it('ignores discovery after leaving the chooser', async () => {
    let finish!: (value: unknown) => void;
    window.pywebview = { api: { discover_setup_servers: vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve; })) } };
    render(<SetupWizard onComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    fireEvent.click(screen.getByRole('button', { name: /Use a cloud API/ }));
    finish({ servers: [{ provider: 'ollama', label: 'Late', reachable: true, models: [] }] });
    await waitFor(() => expect(screen.queryByText('Late found')).toBeNull());
});
