// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import RoutinesWorkspace from './RoutinesWorkspace';

afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
});

it('updates in place and preserves the draft when saving fails', async () => {
    const routine = { id: 'routine-1', name: 'Original', description: 'Setup', steps: [
        { tool_name: 'get_datetime', label: 'Check time', params: {} },
    ] };
    const update = vi.fn().mockResolvedValueOnce({ success: false }).mockResolvedValueOnce({ success: true });
    const remove = vi.fn();
    const create = vi.fn();
    vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
    window.pywebview = { api: {
        get_routines: vi.fn().mockResolvedValue([routine]),
        list_tool_catalog: vi.fn().mockResolvedValue([]),
        update_workflow: update, delete_workflow: remove, create_workflow: create,
    } } as unknown as typeof window.pywebview;

    render(<RoutinesWorkspace />);
    fireEvent.click(await screen.findByTitle('Edit'));
    fireEvent.change(screen.getByPlaceholderText('Saved action name'), { target: { value: 'Revised' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/Could not save changes/);
    expect((screen.getByPlaceholderText('Saved action name') as HTMLInputElement).value).toBe('Revised');
    expect(remove).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText('What does this do? (optional)'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Revised' })).toBeTruthy());
    expect(update).toHaveBeenLastCalledWith('routine-1', 'Revised', '', routine.steps);
    expect(remove).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
});
