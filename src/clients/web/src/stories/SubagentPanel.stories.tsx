import type { Meta, StoryObj } from '@storybook/react-vite';
import SubagentPanel from '../components/data-display/SubagentPanel';

const meta = {
    title: 'Data Display/SubagentPanel',
    component: SubagentPanel,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'A self-contained, collapsible panel for one subagent invocation. Isolates a ' +
                    'delegated specialist\'s thoughts and tool calls from the main agent\'s stream ' +
                    '(the Claude-Code "Task" dropdown). Collapsed by default; the body is the ' +
                    'recursively-rendered subagent activity, passed as children.',
            },
        },
    },
} satisfies Meta<typeof SubagentPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

const Body = () => (
    <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.7)', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div>💭 Reasoning: I should search recent sources, then summarize.</div>
        <div>🔧 search_web("wedding venues near Lansing") → 8 results</div>
        <div>🔧 read_url_content(...) → extracted</div>
        <div>💬 Here is a shortlist of three venues…</div>
    </div>
);

export const Running: Story = {
    name: 'Running',
    args: {
        agentName: 'Research Agent',
        displayText: 'Enlisting research specialist…',
        status: 'pending',
        children: <Body />,
    },
};

export const Done: Story = {
    name: 'Done (success)',
    args: {
        agentName: 'Research Agent',
        displayText: 'Researched wedding venues',
        status: 'success',
        children: <Body />,
    },
};

export const Failed: Story = {
    name: 'Failed',
    args: {
        agentName: 'Browser Agent',
        displayText: 'Automating browser',
        status: 'error',
        children: <Body />,
    },
};

export const NoBody: Story = {
    name: 'No body (not expandable)',
    args: {
        agentName: 'Code Agent',
        status: 'success',
    },
};

export const WithPrompt: Story = {
    name: 'With Prompt (collapsed)',
    args: {
        agentName: 'Research Specialist',
        prompt: 'Search the web for top 5 affordable wedding venues in Lansing, MI with outdoor gardens.',
        status: 'pending',
        children: <Body />,
    },
};

export const ExpandedWithPrompt: Story = {
    name: 'With Prompt (expanded)',
    args: {
        agentName: 'Research Specialist',
        prompt: 'Find the top 5 wedding venues near Lansing with capacity > 150 guests and outdoor ceremonies.',
        status: 'success',
        defaultExpanded: true,
        children: <Body />,
    },
};

export const LongPrompt: Story = {
    name: 'Long Prompt (clamped with toggle)',
    args: {
        agentName: 'Code Specialist',
        prompt: `Create a single-page interactive Canvas course schedule dashboard.
Requirements:
1. Include navigation tabs for Modules, Quizzes, and Due Dates.
2. Render color-coded urgency badges (red for overdue, yellow for upcoming, green for completed).
3. Implement a search bar that filters table entries in real-time.
4. Support dark and light theme toggles with smooth CSS transitions.
5. Provide accessible keyboard navigation and ARIA attributes for screen readers.`,
        status: 'success',
        defaultExpanded: true,
        children: <Body />,
    },
};

