// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ModelsTab from './ModelsTab';
import { useAppStore } from '../../../store';

afterEach(() => { cleanup(); delete window.pywebview; useAppStore.setState({ apiReady: false }); });

it('opens the same model setup and returns without saving a selection', async () => {
    const save = vi.fn();
    window.pywebview = { api: {
        discover_setup_servers: vi.fn().mockResolvedValue({ servers: [] }),
        save_provider_choice: save,
    } };
    useAppStore.setState({ apiReady: true });
    render(<ModelsTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect or set up a model' }));
    await screen.findByRole('heading', { name: 'Welcome to Chad' });
    fireEvent.click(screen.getByRole('button', { name: 'Return to Settings' }));
    expect(screen.queryByRole('heading', { name: 'Welcome to Chad' })).toBeNull();
    expect(save).not.toHaveBeenCalled();
});
