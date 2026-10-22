import type { Meta, StoryObj } from '@storybook/react-vite';
import { fn } from 'storybook/test';
import PlanCard from '../components/data-display/PlanCard';
import type { PlanStep } from '../store';

const meta = {
    title: 'Data Display/PlanCard',
    component: PlanCard,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'Collapsible plan card shown in the chat stream when the planner emits an execution plan. ' +
                    'Rendered via `execution_plan` thinking events — the bug this component caused (plan stripped ' +
                    'but card never shown in local mode) was fixed by subscribing EventBridge to ExecutionPlanEvent.',
            },
        },
    },
    args: {
        onApprove: fn(),
        onCancel: fn(),
    },
} satisfies Meta<typeof PlanCard>;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Fixture data ────────────────────────────────────────────────────────────

const STEPS_ALL_PENDING: PlanStep[] = [
    { id: 's1', description: 'Gather requirements and analyse scope', status: 'pending' },
    { id: 's2', description: 'Design the data model and API surface', status: 'pending' },
    { id: 's3', description: 'Implement the backend service layer', status: 'pending', tools: ['write_file', 'run_bash'] },
    { id: 's4', description: 'Write unit and integration tests', status: 'pending', tools: ['run_bash'] },
    { id: 's5', description: 'Review, polish, and ship', status: 'pending' },
];

const STEPS_IN_PROGRESS: PlanStep[] = [
    { id: 's1', description: 'Gather requirements and analyse scope', status: 'completed' },
    { id: 's2', description: 'Design the data model and API surface', status: 'completed' },
    { id: 's3', description: 'Implement the backend service layer', status: 'running', tools: ['write_file', 'run_bash'] },
    { id: 's4', description: 'Write unit and integration tests', status: 'pending', tools: ['run_bash'] },
    { id: 's5', description: 'Review, polish, and ship', status: 'pending' },
];

const STEPS_COMPLETED: PlanStep[] = [
    { id: 's1', description: 'Gather requirements and analyse scope', status: 'completed' },
    { id: 's2', description: 'Design the data model and API surface', status: 'completed' },
    { id: 's3', description: 'Implement the backend service layer', status: 'completed', tools: ['write_file', 'run_bash'] },
    { id: 's4', description: 'Write unit and integration tests', status: 'completed', tools: ['run_bash'] },
    { id: 's5', description: 'Review, polish, and ship', status: 'completed' },
];

const STEPS_WITH_FAILURE: PlanStep[] = [
    { id: 's1', description: 'Gather requirements', status: 'completed' },
    { id: 's2', description: 'Fetch dependencies', status: 'completed' },
    { id: 's3', description: 'Run build pipeline', status: 'failed', tools: ['run_bash'] },
    { id: 's4', description: 'Deploy to staging', status: 'pending' },
];

// ─── Stories ─────────────────────────────────────────────────────────────────

/**
 * Freshly proposed plan — all steps pending, awaiting user approval.
 * Shows the Proceed / Cancel approval buttons.
 */
export const AwaitingApproval: Story = {
    name: 'Awaiting Approval (active)',
    args: {
        plan: STEPS_ALL_PENDING,
        goal: 'Build and ship the new plan rendering feature end-to-end.',
        isActive: true,
        isHistory: false,
    },
};

/**
 * Plan is running — 2 of 5 steps complete, step 3 spinning.
 */
export const InProgress: Story = {
    name: 'In Progress',
    args: {
        plan: STEPS_IN_PROGRESS,
        goal: 'Build and ship the new plan rendering feature end-to-end.',
        isActive: true,
        isHistory: false,
    },
};

/**
 * All steps done — card in completed / green state.
 */
export const AllCompleted: Story = {
    name: 'All Completed',
    args: {
        plan: STEPS_COMPLETED,
        isActive: false,
        isHistory: false,
    },
};

/**
 * One step failed — card in red / failed state.
 */
export const WithFailure: Story = {
    name: 'With Failure',
    args: {
        plan: STEPS_WITH_FAILURE,
        goal: 'Deploy the latest release to staging.',
        isActive: false,
        isHistory: false,
    },
};

/**
 * Plan rendered in history context (collapsed by default, no approval buttons).
 */
export const HistoryCollapsed: Story = {
    name: 'History (collapsed)',
    args: {
        plan: STEPS_COMPLETED,
        goal: 'Build and ship the new plan rendering feature end-to-end.',
        isActive: false,
        isHistory: true,
    },
};

/**
 * Long plan — tests the +N overflow indicator on the progress pills (>6 steps).
 */
export const LongPlan: Story = {
    name: 'Long Plan (8 steps)',
    args: {
        plan: [
            ...STEPS_ALL_PENDING,
            { id: 's6', description: 'Update documentation and changelog', status: 'pending' },
            { id: 's7', description: 'Create release branch and tag', status: 'pending' },
            { id: 's8', description: 'Notify stakeholders', status: 'pending' },
        ],
        goal: 'Full release cycle from planning to stakeholder notification.',
        isActive: true,
        isHistory: false,
    },
};

/**
 * Minimal plan — single step edge case.
 */
export const SingleStep: Story = {
    name: 'Single Step',
    args: {
        plan: [{ id: 's1', description: 'Run the test suite', status: 'running', tools: ['run_bash'] }],
        isActive: true,
        isHistory: false,
    },
};

/**
 * No goal string provided — header-only variant.
 */
export const NoGoal: Story = {
    name: 'No Goal',
    args: {
        plan: STEPS_IN_PROGRESS,
        isActive: true,
        isHistory: false,
    },
};
