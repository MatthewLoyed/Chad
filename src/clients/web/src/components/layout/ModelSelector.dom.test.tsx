// @vitest-environment jsdom
/**
 * Guards the model picker from growing unbounded when many local models
 * are discovered — the local list must be a scroll container inside a
 * viewport-capped dropdown.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ModelSelector from './ModelSelector';
import { useAppStore } from '../../store';
import type { LocalModelInfo } from '../../store';

const here = dirname(fileURLToPath(import.meta.url));

afterEach(() => {
    cleanup();
    useAppStore.setState({
        availableLocalModels: [],
        availableCloudModels: [],
        activeModel: null,
        isLocalScanning: false,
    });
});

function makeLocal(i: number): LocalModelInfo {
    return {
        name: `model-${i}`,
        display_name: `Model ${i}`,
        provider: 'lmstudio',
        supports_tools: true,
        tools_verified: true,
        size_hint: `${i}B`,
        base_url: 'http://localhost:1234/v1',
        api_key: '',
    };
}

describe('ModelSelector local list overflow', () => {
    it('marks the local list as a scroll container when the menu opens', () => {
        useAppStore.setState({
            availableCloudModels: [{ id: 'gemini', name: 'Gemini 2.0 Flash' }],
            availableLocalModels: Array.from({ length: 20 }, (_, i) => makeLocal(i)),
            activeModel: {
                mode: 'local',
                name: 'model-0',
                provider: 'lmstudio',
                supports_tools: true,
                tools_verified: true,
                thinking_enabled: null,
                model_settings: {},
            },
            isLocalScanning: false,
        });

        render(<ModelSelector />);
        fireEvent.click(screen.getByTitle(/Active:/));

        const localList = document.querySelector('.model-selector__list--local');
        expect(localList).toBeTruthy();
        expect(localList?.getAttribute('data-scrollable')).toBe('true');
        expect(screen.getByText('Model 19')).toBeTruthy();
    });

    it('caps dropdown height and scrolls the local list in CSS', () => {
        const css = readFileSync(join(here, 'ModelSelector.css'), 'utf8');
        expect(css).toMatch(/\.model-selector__dropdown\s*\{[^}]*max-height:\s*min\(420px/s);
        expect(css).toMatch(
            /\.model-selector__list--local\s*\{[^}]*overflow-y:\s*auto/s
        );
        expect(css).toMatch(
            /\.model-selector__list--local\s*\{[^}]*min-height:\s*0/s
        );
    });
});


it('finishes empty discovery without automatically restarting it', async () => {
    const discover = vi.fn().mockResolvedValue([]);
    Object.assign(window, { pywebview: { api: { discover_local_models: discover } } });
    render(<ModelSelector />);
    fireEvent.click(screen.getByTitle(/Active:/));
    await waitFor(() => expect(screen.getByText('Could not find any local models.')).toBeTruthy());
    expect(discover).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Cloud models require your own API key/)).toBeTruthy();
    Object.assign(window, { pywebview: undefined });
});
