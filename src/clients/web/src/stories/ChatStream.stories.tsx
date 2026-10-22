import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { useAppStore } from '../store';
import ChatStream from '../components/layout/ChatStream';
import type { ChatMessage, ThinkingEvent } from '../store';
import { initialTurnState, type TurnState, type TurnPart } from '../protocol/streamReducer';

/** Build a TurnState (the render source) from a flat ThinkingEvent list for previews. */
function buildStreamTurnFromEvents(events: ThinkingEvent[]): TurnState {
    const parts: TurnPart[] = [];
    let rid = 0;
    let tid = 0;
    events.forEach((e) => {
        if (e.type === 'reasoning' || e.type === 'thought' || e.type === 'thinking') {
            parts.push({ kind: 'reasoning', id: `r-${rid++}`, text: e.text ?? '', ended: true, agent_name: e.agent_name });
        } else if (e.type === 'speech') {
            parts.push({ kind: 'text', id: `t-${tid++}`, text: e.text ?? '', ended: true });
        } else if (e.type === 'tool_start') {
            parts.push({ kind: 'tool', id: e.correlation_id ?? `tool-${parts.length}`, name: e.name ?? 'tool', args: e.params, display_text: e.display_text, ok: e.success, result: e.result });
        } else if (e.type === 'execution_plan' && e.plan) {
            parts.push({ kind: 'plan', id: e.plan_id ?? 'plan', steps: e.plan, goal: e.goal });
        }
    });
    return { ...initialTurnState(), turn_id: 'story', lifecycle: 'running', parts };
}

// ─── Store seed decorator ─────────────────────────────────────────────────────
// Seeds the Zustand store before each story. Because Zustand is a singleton we
// call setState() directly — no Provider needed.

interface StorySeedProps {
    messages: ChatMessage[];
    activeTurnEvents?: ThinkingEvent[];
    status?: string;
    streamingResponse?: string | null;
}

function StorySeed({
    messages,
    activeTurnEvents = [],
    status = 'ready',
    streamingResponse = null,
    children,
}: StorySeedProps & { children: React.ReactNode }) {
    useEffect(() => {
        useAppStore.setState({
            chatMessages: messages,
            streamTurn: activeTurnEvents.length > 0 ? buildStreamTurnFromEvents(activeTurnEvents) : null,
            status: status as never,
            streamingResponse,
            apiReady: true,
            activeConversationId: 'story-conv-1',
            tokenUsage: null,
            turnStartTime: undefined,
        });
        // Reset on unmount
        return () => {
            useAppStore.setState({
                chatMessages: [],
                streamTurn: null,
                status: 'ready',
                streamingResponse: null,
            });
        };
    }, []);  // eslint-disable-line react-hooks/exhaustive-deps

    return <>{children}</>;
}

// ─── Meta ────────────────────────────────────────────────────────────────────

const meta = {
    title: 'Layout/ChatStream',
    component: ChatStream,
    args: {},
    tags: ['autodocs'],
    parameters: {
        layout: 'fullscreen',
        docs: {
            description: {
                component:
                    'The main chat message list. Renders user messages, AI responses (via AIResponseRenderer), ' +
                    'PlanCards from execution_plan events, ThinkingWidgets, ToolStackBubbles, and live streaming text.',
            },
        },
    },
    decorators: [
        (Story, ctx) => {
            const { messages = [], activeTurnEvents = [], status = 'ready', streamingResponse = null } =
                (ctx.parameters.storeSeed ?? {}) as Partial<StorySeedProps>;
            return (
                <div style={{ height: '600px', display: 'flex', flexDirection: 'column' }}>
                    <StorySeed
                        messages={messages}
                        activeTurnEvents={activeTurnEvents}
                        status={status}
                        streamingResponse={streamingResponse}
                    >
                        <Story />
                    </StorySeed>
                </div>
            );
        },
    ],
} satisfies Meta<typeof ChatStream>;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Fixture data ─────────────────────────────────────────────────────────────

const t = (offset = 0) => new Date(Date.now() - 60000 + offset);

const userMsg = (text: string, id: string): ChatMessage => ({
    id, role: 'user', text, timestamp: t(),
});

const aiMsg = (text: string, id: string, thoughts?: ThinkingEvent[]): ChatMessage => ({
    id, role: 'ai', text, timestamp: t(1000),
    thoughts,
    completion_tokens: 128,
    speed: 42.5,
    ttft: 0.3,
    stop_reason: 'task_completed',
});

const planThought = (goal: string): ThinkingEvent => ({
    type: 'execution_plan',
    plan: [
        { id: 's1', description: 'Research existing implementations', status: 'completed' },
        { id: 's2', description: 'Write the integration', status: 'running' },
        { id: 's3', description: 'Run tests', status: 'pending' },
    ],
    goal,
    plan_id: 'plan-story-1',
    timestamp: Date.now() - 3000,
});

const reasoningThought = (text: string): ThinkingEvent => ({
    type: 'reasoning',
    text,
    timestamp: Date.now() - 4000,
});

const toolStart = (name: string, id: string): ThinkingEvent => ({
    type: 'tool_start',
    name,
    correlation_id: id,
    params: { query: 'Storybook 8 Vite compatibility' },
    timestamp: Date.now() - 2000,
});

const toolEnd = (name: string, id: string, success = true): ThinkingEvent => ({
    type: 'tool_end',
    name,
    correlation_id: id,
    success,
    result: success ? '10 results found' : 'Network timeout',
    timestamp: Date.now() - 1000,
});

const speechThought = (text: string): ThinkingEvent => ({
    type: 'speech',
    text,
    timestamp: Date.now() - 500,
});

// ─── Stories ──────────────────────────────────────────────────────────────────

/**
 * Empty state — no messages, ready status.
 */
export const Empty: Story = {
    name: 'Empty',
    parameters: { storeSeed: { messages: [], status: 'ready' } },
};

/**
 * Simple back-and-forth conversation, no tools or plan.
 */
export const SimpleConversation: Story = {
    name: 'Simple Conversation',
    parameters: {
        storeSeed: {
            messages: [
                userMsg('Hey Chad, what can you help me with?', 'u1'),
                aiMsg(
                    "I can help with coding, research, file management, scheduling, and much more! " +
                    "I have access to tools like web search, file read/write, and code execution. What would you like to work on?",
                    'a1'
                ),
                userMsg('Can you fix the plan card rendering bug?', 'u2'),
                aiMsg(
                    "Found it! The `EventBridge` in `src/clients/desktop/event_bridge.py` subscribed to 18 event " +
                    "types but was missing `ExecutionPlanEvent`. Fixed by adding the import, subscription, and handler. " +
                    "The plan card now surfaces correctly in local mode.",
                    'a2'
                ),
            ],
            status: 'ready',
        },
    },
};

/**
 * Response that includes an execution plan (production bug scenario).
 * The plan card should appear above the speech text.
 */
export const WithExecutionPlan: Story = {
    name: 'With Execution Plan',
    parameters: {
        storeSeed: {
            messages: [
                userMsg("Build me a UI testing suite for the React frontend.", 'u1'),
                aiMsg(
                    "I've laid out the plan above. I'll start with the Storybook installation.",
                    'a1',
                    [
                        reasoningThought("The user wants a professional UI testing suite. Best approach: Storybook 8 + Playwright."),
                        planThought("Build and ship the UI testing suite (Storybook + Playwright)"),
                        speechThought("I've laid out the plan above. I'll start with the Storybook installation."),
                    ]
                ),
            ],
            status: 'ready',
        },
    },
};

/**
 * Response with tool calls — shows ToolStackBubble in history.
 */
export const WithToolCalls: Story = {
    name: 'With Tool Calls',
    parameters: {
        storeSeed: {
            messages: [
                userMsg("Search for the latest Storybook docs and summarize them.", 'u1'),
                aiMsg(
                    "Here's a summary of the Storybook 8 documentation:\n\n" +
                    "- **Framework**: Use `@storybook/react-vite` for Vite projects\n" +
                    "- **Addons**: `@storybook/addon-essentials` includes controls, docs, backgrounds\n" +
                    "- **Testing**: `@storybook/addon-interactions` + `@storybook/test` for play functions\n" +
                    "- **Visual regression**: Playwright snapshots or Chromatic",
                    'a1',
                    [
                        toolStart('web_search', 'tool-1'),
                        toolEnd('web_search', 'tool-1', true),
                        toolStart('web_fetch', 'tool-2'),
                        toolEnd('web_fetch', 'tool-2', true),
                        speechThought(
                            "Here's a summary of the Storybook 8 documentation:\n\n" +
                            "- **Framework**: Use `@storybook/react-vite` for Vite projects\n" +
                            "- **Addons**: `@storybook/addon-essentials` includes controls, docs, backgrounds\n" +
                            "- **Testing**: `@storybook/addon-interactions` + `@storybook/test` for play functions\n" +
                            "- **Visual regression**: Playwright snapshots or Chromatic"
                        ),
                    ]
                ),
            ],
            status: 'ready',
        },
    },
};

/**
 * Active turn with conflicting plan_id values — must render exactly one PlanCard.
 * Regression story for the duplicate Execution Plan bug.
 */
export const ActiveDuplicatePlanIds: Story = {
    name: 'Active — Duplicate Plan IDs (regression)',
    parameters: {
        storeSeed: {
            messages: [
                userMsg(
                    'create a plan to check my email, turn on my lamp, create a website about messi and ronaldo',
                    'u1',
                ),
            ],
            activeTurnEvents: [
                reasoningThought('Thought for 1.0 seconds'),
                {
                    type: 'execution_plan',
                    plan_id: 'session-uuid-conflict',
                    goal: 'Multi-step home and web task',
                    plan: [
                        { id: 's1', description: 'Check email', tools: ['list_emails'], status: 'pending' },
                        { id: 's2', description: 'Turn on lamp', tools: ['control_device'], status: 'pending' },
                        { id: 's3', description: 'Research Messi vs Ronaldo', tools: ['web_search'], status: 'pending' },
                        { id: 's4', description: 'Build high-end website', tools: ['write_file'], status: 'pending' },
                        { id: 's5', description: 'Verify deliverables', tools: ['read_file'], status: 'pending' },
                    ],
                    timestamp: Date.now() - 2000,
                },
                {
                    type: 'execution_plan',
                    plan_id: 'turn_3_1718300000000',
                    goal: 'Multi-step home and web task',
                    plan: [
                        { id: 's1', description: 'Check email', tools: ['list_emails'], status: 'pending' },
                        { id: 's2', description: 'Turn on lamp', tools: ['control_device'], status: 'pending' },
                        { id: 's3', description: 'Research Messi vs Ronaldo', tools: ['web_search'], status: 'pending' },
                        { id: 's4', description: 'Build high-end website', tools: ['write_file'], status: 'pending' },
                        { id: 's5', description: 'Verify deliverables', tools: ['read_file'], status: 'pending' },
                    ],
                    timestamp: Date.now() - 1000,
                },
            ],
            status: 'thinking',
        },
    },
};

/**
 * Active thinking state — ThinkingWidget spinning, no response yet.
 */
export const ActiveThinking: Story = {
    name: 'Active — Thinking',
    parameters: {
        storeSeed: {
            messages: [userMsg("Write a Playwright config for visual regression testing.", 'u1')],
            activeTurnEvents: [
                reasoningThought("Let me think about the best Playwright configuration for this project..."),
            ],
            status: 'thinking',
        },
    },
};
