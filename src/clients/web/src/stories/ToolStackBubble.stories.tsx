import type { Meta, StoryObj } from '@storybook/react-vite';
import ToolStackBubble from '../components/data-display/ToolStackBubble';
import type { StackedToolItem } from '../components/data-display/ToolStackBubble';
import type { ThinkingEvent } from '../store';

const meta = {
    title: 'Data Display/ToolStackBubble',
    component: ToolStackBubble,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'Groups consecutive same-category tool calls into a single collapsible bubble. ' +
                    'Single-item stacks fall through to a plain ToolBubble. ' +
                    'Categories: filesystem, browser, system, delegation, code, etc.',
            },
        },
    },
} satisfies Meta<typeof ToolStackBubble>;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Fixture helpers ─────────────────────────────────────────────────────────

const makeEvent = (
    name: string,
    params: Record<string, unknown> = {},
    overrides: Partial<ThinkingEvent> = {}
): ThinkingEvent => ({
    type: 'tool_start',
    name,
    params,
    correlation_id: `${name}-${Math.random().toString(36).slice(2, 8)}`,
    timestamp: Date.now(),
    ...overrides,
});

const makeItem = (
    name: string,
    status: StackedToolItem['status'] = 'success',
    params: Record<string, unknown> = {}
): StackedToolItem => ({
    correlationId: `${name}-${Math.random().toString(36).slice(2, 8)}`,
    startEvent: makeEvent(name, params),
    status,
});

// ─── Stories ─────────────────────────────────────────────────────────────────

/**
 * Single filesystem write — falls through to ToolBubble.
 */
export const SingleFilesystem: Story = {
    name: 'Single — Filesystem (ToolBubble fallback)',
    args: {
        category: 'filesystem',
        items: [makeItem('write_file', 'success', { path: 'src/event_bridge.py' })],
    },
};

/**
 * Stacked filesystem writes — 3 files modified in one batch.
 */
export const StackedFilesystem: Story = {
    name: 'Stacked — Filesystem (3 files)',
    args: {
        category: 'filesystem',
        items: [
            makeItem('write_file', 'success', { path: 'src/event_bridge.py' }),
            makeItem('write_file', 'success', { path: 'src/stories/PlanCard.stories.tsx' }),
            makeItem('write_file', 'pending', { path: 'src/stories/ToolStackBubble.stories.tsx' }),
        ],
    },
};

/**
 * Stacked browser tools — several web searches and fetches.
 */
export const StackedBrowser: Story = {
    name: 'Stacked — Browser (Web Research)',
    args: {
        category: 'browser',
        items: [
            makeItem('web_search', 'success', { query: 'Storybook 8 Vite 7 compatibility' }),
            makeItem('web_fetch', 'success', { url: 'https://storybook.js.org/docs' }),
            makeItem('web_search', 'success', { query: 'Playwright visual regression react' }),
            makeItem('web_fetch', 'error', { url: 'https://example.com/timeout' }),
        ],
    },
};

/**
 * Single tool still running (pending status).
 */
export const SinglePending: Story = {
    name: 'Single — Pending (running)',
    args: {
        category: 'system',
        items: [makeItem('run_bash', 'pending', { command: 'npm install storybook@latest' })],
    },
};

/**
 * Mixed status stack — some success, one error.
 */
export const MixedStatus: Story = {
    name: 'Mixed Status',
    args: {
        category: 'code',
        items: [
            makeItem('run_python', 'success', { script: 'tests/unit/test_registry.py' }),
            makeItem('run_python', 'success', { script: 'tests/unit/test_langgraph.py' }),
            makeItem('run_python', 'error', { script: 'tests/integration/test_assistant.py' }),
        ],
    },
};

/**
 * Delegation / agent orchestration stack.
 */
export const DelegationStack: Story = {
    name: 'Delegation — Agent Orchestration',
    args: {
        category: 'delegation',
        items: [
            makeItem('delegate_to_agent', 'success', { agent: 'researcher', task: 'Find Storybook docs' }),
            makeItem('delegate_to_agent', 'success', { agent: 'coder', task: 'Write story files' }),
            makeItem('delegate_to_agent', 'pending', { agent: 'reviewer', task: 'Run visual tests' }),
        ],
    },
};

/**
 * Large stack — tests the +N overflow indicator (>5 items).
 */
export const LargeStack: Story = {
    name: 'Large Stack (7 items)',
    args: {
        category: 'filesystem',
        items: Array.from({ length: 7 }, (_, i) =>
            makeItem('read_file', i < 5 ? 'success' : 'pending', { path: `src/file_${i + 1}.py` })
        ),
    },
};
