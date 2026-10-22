import type { Meta, StoryObj } from '@storybook/react-vite';
import ThinkingWidget from '../components/data-display/ThinkingWidget';
import type { ThinkingEvent } from '../store';

const meta = {
    title: 'Data Display/ThinkingWidget',
    component: ThinkingWidget,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'Collapsible thinking/reasoning accordion that appears above AI responses. ' +
                    'Shows streamed thought tokens, tool events, and timing. ' +
                    'Auto-opens when active and closes on turn completion.',
            },
        },
    },
} satisfies Meta<typeof ThinkingWidget>;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Fixture helpers ─────────────────────────────────────────────────────────

const ts = (offsetMs = 0): number => Date.now() - 5000 + offsetMs;

const thought = (text: string, ms = 0): ThinkingEvent => ({
    type: 'thought',
    text,
    timestamp: ts(ms),
});

const reasoning = (text: string, ms = 0): ThinkingEvent => ({
    type: 'reasoning',
    text,
    timestamp: ts(ms),
});

// ─── Stories ─────────────────────────────────────────────────────────────────

/**
 * Active, still generating — shows live elapsed timer.
 */
export const ActiveStreaming: Story = {
    name: 'Active — Streaming',
    args: {
        events: [],
        isActive: true,
        isHistory: false,
    },
};

/**
 * Active with buffered thought tokens already accumulated.
 */
export const ActiveWithThoughts: Story = {
    name: 'Active — With Thoughts',
    args: {
        events: [
            thought("Let me think about this carefully.", 0),
            thought("The user is asking about the EventBridge bug. I should check what events are subscribed.", 800),
            reasoning("Looking at the subscribe() method... I see 18 subscriptions but ExecutionPlanEvent is missing.", 1600),
        ],
        isActive: true,
        isHistory: false,
    },
};

/**
 * Completed thinking block — closed by default, shows duration.
 */
export const CompletedClosed: Story = {
    name: 'Completed — Closed',
    args: {
        events: [
            thought("The user wants to add visual regression testing.", 0),
            thought("Best option is Storybook 8 + Playwright snapshots.", 400),
            reasoning("I'll install the packages, write stories for all key components, then wire up Playwright.", 900),
            reasoning("Key components to cover: PlanCard, ThinkingWidget, MarkdownMessage, ToolStackBubble, ArtifactCard.", 1400),
        ],
        isActive: false,
        isHistory: false,
        durationMs: 2800,
    },
};

/**
 * Completed, in history — same as closed but marks the context.
 */
export const HistoryEntry: Story = {
    name: 'History Entry',
    args: {
        events: [
            thought("Reviewing the previous turn's context.", 0),
            reasoning("I should summarize the key changes made so far.", 600),
        ],
        isActive: false,
        isHistory: true,
        durationMs: 1200,
    },
};

/**
 * Long reasoning block — many tokens to show scrollability.
 */
export const DeepReasoning: Story = {
    name: 'Deep Reasoning',
    args: {
        events: [
            reasoning(
                `I need to understand the full architecture before I make changes.

The EventBridge subscribes to backend events and forwards them to the MainWindow via JavaScript calls. Looking at the subscribe() method, it currently registers 18 event types.

The ExecutionPlanEvent is published in two places:
1. supervisor.py — when the planner node parses a [[PLAN]] block from the LLM response
2. turn_coordinator.py — at the end of a turn when extracting plan directives from the final response

In cloud/remote mode, limb_coordinator.py handles this by subscribing to ExecutionPlanEvent and broadcasting it over WebSocket to the gui_limb, which then calls push_thinking_event. This path works correctly.

In local/desktop mode, the EventBridge should handle it — but the subscription is missing. That's the bug.

The fix: add ExecutionPlanEvent to the imports and the subscribe() call, and add a _on_event_execution_plan handler that calls self._broadcast("push_thinking_event", {...}).

I should also verify that the frontend's AIResponseRenderer handles the execution_plan event type — looking at ChatStream.tsx around line 820, it does: node.type === 'execution_plan' && node.plan triggers a PlanCard render.

So the only missing piece is the EventBridge subscription. Three lines of code to fix a complete UI regression.`,
                0
            ),
        ],
        isActive: false,
        isHistory: false,
        durationMs: 4200,
    },
};

/**
 * Empty events — just the active spinner, no buffered tokens yet.
 */
export const EmptyActive: Story = {
    name: 'Empty Active (just started)',
    args: {
        events: [],
        isActive: true,
        isHistory: false,
    },
};
