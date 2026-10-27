// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ToolSelectorSettings from './ToolSelectorSettings';
import type { LocalModelInfo } from '../../../store';

afterEach(() => { cleanup(); delete window.pywebview; });

it('saves a separate selector choice and retains an offline configured model', async () => {
    const save = vi.fn().mockResolvedValue(true);
    window.pywebview = { api: {
        get_tool_selector_model: vi.fn().mockResolvedValue({ model: 'offline', provider: 'lmstudio' }),
        set_tool_selector_model: save,
    } };
    const model = { name: 'small', provider: 'ollama', display_name: 'Small selector' } as LocalModelInfo;
    render(<ToolSelectorSettings models={[model]} />);
    await screen.findByText('lmstudio:offline (configured)');
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'ollama:small' } });
    fireEvent.click(screen.getByText('Save tool selector'));
    await waitFor(() => expect(save).toHaveBeenCalledWith('small', 'ollama'));
    await screen.findByText('Tool selector model saved.');
});

it('reports a failed save without claiming the choice was saved', async () => {
    window.pywebview = { api: {
        get_tool_selector_model: vi.fn().mockResolvedValue({ model: 'old', provider: 'lmstudio' }),
        set_tool_selector_model: vi.fn().mockResolvedValue(false),
    } };
    render(<ToolSelectorSettings models={[{ name: 'small', provider: 'lmstudio' } as LocalModelInfo]} />);
    await screen.findByText('lmstudio:old (configured)');
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'lmstudio:small' } });
    fireEvent.click(screen.getByText('Save tool selector'));
    await screen.findByText('Could not save the tool selector model. Try again.');
    expect(screen.queryByText('Tool selector model saved.')).toBeNull();
});
