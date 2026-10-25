import type { Meta, StoryObj } from '@storybook/react-vite';
import SetupWizard from '../components/setup/SetupWizard';
import bundleFixture from './fixtures/setupBundle.json';

const meta = {
    title: 'Setup/First conversation', component: SetupWizard,
    args: { onComplete: () => {} },
    parameters: { layout: 'fullscreen' },
    decorators: [(Story, context) => {
        const bundled = context.name === 'Recommended bundles';
        window.pywebview = { api: {
            assess_model_hardware: async () => bundled ? bundleFixture : ({
                success: true, recommended_id: 'example-chat', target_met: true,
                hardware: { cpu: 'Example CPU', physical_cores: 8, ram_gb: 32,
                    available_ram_gb: 20, storage_free_gb: 100,
                    gpus: [{ name: 'Example GPU', vram_gb: 12 }], warnings: [] },
                assumptions: 'One chat model with operating system and context headroom.',
                models: [
                    { id: 'example-chat', name: 'Example Chat 8B', fits: true },
                    { id: 'example-small', name: 'Example Chat 4B', fits: true },
                    { id: 'example-large', name: 'Example Chat 32B', fits: false },
                ].map(model => ({ ...model, quantization: 'Q4_K_M', weights_gb: 5,
                    kv_gb: 1, required_gb: 7, mode: 'gpu', reasons: [], source: '',
                    speed: { kind: 'estimate', low: 45, high: 65, note: 'Heuristic. Validate locally.' },
                    settings: { gpu_offload: 'max', temperature: 0.7, top_p: 0.8, thinking: 'off', advanced: 'Keep engine defaults.' } })),
            }),
            test_setup_bundle: async () => ({ success: true, reply: 'Hello from the main model.', validation_token: 'fictional-validation' }),
            save_setup_bundle: async () => ({ success: true }),
            get_model_setup_job: async () => ({ running: false, done: false }),
            probe_local_backend: async () => bundled ? { reachable: true, bundle_models: { 'bonsai-27b-q1': 'prism-ml/bonsai-27b' }, models: [{ id: 'prism-ml/bonsai-27b', display_name: 'Bonsai-27B' }] } : ({ reachable: true, models: [{ id: 'example-chat', display_name: 'Example Chat 8B' }] }),
            test_setup_model: async () => ({ success: true, reply: 'Hello! This fictional model is ready for a chat.' }),
            save_provider_choice: async () => ({ success: true }),
            start_model_setup_job: async () => ({ success: false, detail: 'Preview only. No download was started.' }),
        } };
        return <Story />;
    }],
} satisfies Meta<typeof SetupWizard>;
export default meta;
type Story = StoryObj<typeof meta>;
export const GuidedSetup: Story = {};

export const RecommendedBundles: Story = { name: 'Recommended bundles' };
