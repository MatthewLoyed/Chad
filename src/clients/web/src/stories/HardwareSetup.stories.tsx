import type { Meta, StoryObj } from '@storybook/react-vite';
import HardwareSetup from '../components/setup/HardwareSetup';

const meta = {
    title: 'Setup/Hardware assessment', component: HardwareSetup,
    args: { baseUrl: 'http://localhost:1234/v1', apiKey: '', modelId: 'example-chat' },
    parameters: { layout: 'centered' },
    decorators: [(Story, context) => {
        const lowMemory = context.name === 'Insufficient memory';
        window.pywebview = { api: {
            assess_model_hardware: async () => ({
                success: true, recommended_id: lowMemory ? null : 'example-chat', target_met: false,
                hardware: { cpu: 'Example CPU', physical_cores: 8, ram_gb: lowMemory ? 4 : 32,
                    available_ram_gb: lowMemory ? 1 : 20, storage_free_gb: 100,
                    gpus: [], warnings: ['No GPU detected. CPU-only recommendations remain available.'] },
                assumptions: 'One chat model shared by auxiliary roles; OS, embedding and KV-cache headroom reserved.',
                models: [{ id: 'example-chat', name: 'Example Chat', quantization: 'Q4_K_M', weights_gb: 3,
                    kv_gb: 1, required_gb: 4.75, mode: 'cpu', fits: !lowMemory,
                    reasons: lowMemory ? ['Insufficient available RAM after OS and embedding headroom.'] : [],
                    speed: { kind: 'unknown', low: null, high: null, note: 'Benchmark required for CPU inference.' },
                    settings: { gpu_offload: 'off', temperature: 0.7, top_p: 0.8, thinking: 'off for interactive speed', advanced: 'Keep engine defaults until validated.' } }],
            }),
            get_model_setup_job: async () => ({ running: false, done: false }),
            start_model_setup_job: async () => ({ success: false, detail: 'Preview only: no model was downloaded or loaded.' }),
        } };
        return <div style={{ width: 'min(480px, 92vw)', color: '#e5e7eb', background: '#16181d', padding: 24 }}><Story /></div>;
    }],
} satisfies Meta<typeof HardwareSetup>;
export default meta;
type Story = StoryObj<typeof meta>;
export const CPUOnly: Story = { name: 'CPU only' };
export const InsufficientMemory: Story = { name: 'Insufficient memory' };
