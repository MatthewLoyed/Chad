// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import OllamaAcquisition from './OllamaAcquisition';
import ProviderSpeedTest from './ProviderSpeedTest';

afterEach(() => { cleanup(); delete window.pywebview; });

it('requires a separate confirmation before a curated Ollama pull', async () => {
    const start = vi.fn().mockResolvedValue({ success: true });
    window.pywebview = { api: {
        assess_ollama_hardware: vi.fn().mockResolvedValue({ models: [{ model: 'fictional:9b', name: 'Fictional 9B', size_bytes: 6e9, fits: true, settings_note: 'Use server defaults.' }] }),
        start_model_setup_job: start,
        get_model_setup_job: vi.fn().mockResolvedValue({ done: true, result: { success: true, note: 'Verified' } }),
    } };
    render(<OllamaAcquisition baseUrl="http://localhost:11434/v1" apiKey="fictional" onReady={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Find an Ollama model for me', hidden: true }));
    fireEvent.click(await screen.findByRole('button', { name: /Download Fictional 9B/, hidden: true }));
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Ollama download', hidden: true }));
    expect(start).toHaveBeenCalledWith('ollama-pull', { model_id: 'fictional:9b', base_url: 'http://localhost:11434/v1', api_key: 'fictional' }, true);
});

it('does not offer local acquisition for a remote inference endpoint', () => {
    window.pywebview = { api: { assess_ollama_hardware: vi.fn() } };
    const { container } = render(<OllamaAcquisition baseUrl="https://models.example.test/v1" apiKey="" onReady={vi.fn()} />);
    expect(container.textContent).toBe('');
});

it('labels a compatible server measurement as full request throughput', async () => {
    const start = vi.fn().mockResolvedValue({ success: true });
    window.pywebview = { api: { start_model_setup_job: start,
        get_model_setup_job: vi.fn().mockResolvedValue({ done: true, result: { success: true, metric: 'request_throughput', stats: { tokens_per_second: 21.5 } } }),
    } };
    render(<ProviderSpeedTest provider="custom" baseUrl="http://localhost:8000/v1" apiKey="" modelId="example" />);
    fireEvent.click(screen.getByText('Measure speed on this computer'));
    fireEvent.click(screen.getByRole('button', { name: 'Measure generation speed…' }));
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm speed test' }));
    expect(await screen.findByText(/Measured 21.5 tokens\/sec · full request throughput/, {}, { timeout: 4000 })).toBeTruthy();
});
