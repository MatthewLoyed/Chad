// @vitest-environment jsdom
/**
 * Guards the model picker from growing unbounded when many local models
 * are discovered — cloud and local models share one viewport-capped scroller.
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

describe('ModelSelector overflow', () => {
    it('puts cloud and local models in the same scroll container', () => {
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

        const dropdown = document.querySelector('.model-selector__dropdown');
        expect(dropdown?.getAttribute('data-scrollable')).toBe('true');
        expect(dropdown?.contains(screen.getByText('Gemini 2.0 Flash'))).toBe(true);
        expect(dropdown?.contains(screen.getByText('Model 19'))).toBe(true);
    });

    it('caps dropdown height and scrolls the whole dropdown in CSS', () => {
        const css = readFileSync(join(here, 'ModelSelector.css'), 'utf8');
        expect(css).toMatch(/\.model-selector__dropdown\s*\{[^}]*max-height:\s*min\(420px/s);
        expect(css).toMatch(
            /\.model-selector__dropdown\s*\{[^}]*overflow-y:\s*auto/s
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
