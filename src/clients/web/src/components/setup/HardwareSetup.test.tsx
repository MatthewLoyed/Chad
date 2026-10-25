// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import HardwareSetup from './HardwareSetup';
import bundleFixture from '../../stories/fixtures/setupBundle.json';

afterEach(() => { cleanup(); delete window.pywebview; });
const assessment = {
    success: true, target_met: false, recommended_id: 'example',
    hardware: { cpu: 'Example CPU', physical_cores: 8, ram_gb: 32, available_ram_gb: 20,
        storage_free_gb: 100, gpus: [], warnings: ['No GPU detected. CPU fallback available.'] },
    assumptions: 'One model, OS and KV headroom.',
    models: [{ id: 'example', name: 'Example Chat', quantization: 'Q4_K_M', weights_gb: 3,
        kv_gb: 1, required_gb: 5, fits: true, reasons: [], mode: 'cpu', source: '',
        speed: { kind: 'unknown', low: null, high: null, note: 'Benchmark required.' },
        settings: { gpu_offload: 'off', temperature: 0.7, top_p: 0.8, thinking: 'off', advanced: 'Use runtime defaults.' } }],
};
function mount(api: NonNullable<NonNullable<Window['pywebview']>['api']>) {
    window.pywebview = { api };
    render(<HardwareSetup baseUrl="http://localhost:1234/v1" apiKey="" modelId="" />);
}

it('scans without downloading, reports unknown speed and requires explicit confirmation', async () => {
    const start = vi.fn().mockResolvedValue({ success: true });
    mount({ assess_model_hardware: vi.fn().mockResolvedValue(assessment), start_model_setup_job: start });
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    await screen.findByText(/Needs a speed test/);
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Download Example Chat' }));
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm download' }));
    await waitFor(() => expect(start).toHaveBeenCalledWith('download', { catalog_id: 'example', storage_path: '' }, true));
});

it('invalidates recommendations after changing context and discards late replies', async () => {
    let resolve!: (value: unknown) => void;
    mount({ assess_model_hardware: vi.fn().mockReturnValue(new Promise(r => { resolve = r; })) });
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    fireEvent.change(screen.getByLabelText('Context tokens'), { target: { value: '32768' } });
    resolve(assessment);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Find a model for me' })).toBeTruthy());
    expect(screen.queryByText('Example Chat · Q4_K_M')).toBeNull();
});

it('explains insufficient memory and disables the incompatible download', async () => {
    mount({ assess_model_hardware: vi.fn().mockResolvedValue({ ...assessment, recommended_id: null,
        models: [{ ...assessment.models[0], fits: false, reasons: ['Insufficient available RAM.'] }] }) });
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    await screen.findByText('Insufficient available RAM.');
    expect(screen.getByRole('button', { name: 'Download Example Chat' })).toHaveProperty('disabled', true);
});

it('keeps manual setup available when assessment fails', async () => {
    mount({ assess_model_hardware: vi.fn().mockRejectedValue(new Error('Probe unavailable')) });
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Probe unavailable');
    expect(screen.getByRole('button', { name: 'Find a model for me' })).toHaveProperty('disabled', false);
});

it('explains one bundled download and never asks for a task questionnaire', async () => {
    const start = vi.fn().mockResolvedValue({ success: true });
    mount({ assess_model_hardware: vi.fn().mockResolvedValue(bundleFixture), start_model_setup_job: start });
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    await screen.findByRole('heading', { name: 'Bonsai-27B' });
    expect(screen.getByText(/Qwen2.5-3B-Instruct — configured automatically/)).toBeTruthy();
    expect(screen.getByText(/Subagents reuse/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Prepare recommended setup' }));
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm model downloads' }));
    await waitFor(() => expect(start).toHaveBeenCalledWith('bundle', { catalog_ids: ['bonsai-27b-q1', 'qwen3.5-9b', 'qwen2.5-3b-instruct'], storage_path: '' }, true));
});

it('uses readable model cards, separates technical details and preserves the recommendation on return', async () => {
    mount({ assess_model_hardware: vi.fn().mockResolvedValue({ ...assessment, recommended_id: 'example', models: [assessment.models[0], { ...assessment.models[0], id: 'too-big', name: 'Oversized Chat', fits: false }] }) });
    fireEvent.click(screen.getByRole('button', { name: 'Find a model for me' }));
    await screen.findByRole('region', { name: 'Suggested model' });
    fireEvent.click(screen.getByText('Compare other models'));
    expect(screen.getByRole('button', { name: /Oversized Chat/ })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: /Example Chat.*Recommended/ }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('Suggested LM Studio settings').closest('details')?.open).toBe(false);
});
