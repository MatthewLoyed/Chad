import { describe, expect, it } from 'vitest';
import { useAppStore } from './store';

describe('addThinkingEvent model_loading', () => {
    it('updates local name/provider without clobbering thinking settings', () => {
        useAppStore.setState({
            activeModel: {
                mode: 'local',
                name: 'google/gemma-4-26b-a4b-qat',
                provider: 'lmstudio',
                supports_tools: true,
                tools_verified: true,
                thinking_enabled: true,
                model_settings: {
                    thinking: true,
                    reasoning_effort: 'medium',
                    thinking_budget: 32768,
                    top_p: 0.8,
                },
            },
            thinkingEvents: [],
            activeTurn: null,
        });

        useAppStore.getState().addThinkingEvent({
            type: 'model_loading',
            name: 'google/gemma-4-26b-a4b-qat',
            provider: 'lmstudio',
            text: 'Loading google/gemma-4-26b-a4b-qat...',
        });

        const model = useAppStore.getState().activeModel;
        expect(model).toMatchObject({
            mode: 'local',
            name: 'google/gemma-4-26b-a4b-qat',
            provider: 'lmstudio',
            supports_tools: true,
            tools_verified: true,
            thinking_enabled: true,
            model_settings: {
                thinking: true,
                reasoning_effort: 'medium',
                thinking_budget: 32768,
                top_p: 0.8,
            },
        });
    });

    it('still seeds a local activeModel when none was set', () => {
        useAppStore.setState({
            activeModel: null,
            thinkingEvents: [],
            activeTurn: null,
        });

        useAppStore.getState().addThinkingEvent({
            type: 'model_loading',
            name: 'qwen/qwen3.5-9b',
            provider: 'lmstudio',
        });

        expect(useAppStore.getState().activeModel).toMatchObject({
            mode: 'local',
            name: 'qwen/qwen3.5-9b',
            provider: 'lmstudio',
            supports_tools: true,
            thinking_enabled: null,
        });
    });
});
