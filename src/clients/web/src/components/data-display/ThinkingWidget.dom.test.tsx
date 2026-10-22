// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ThinkingWidget from './ThinkingWidget';
import { formatThoughtDurationLabel } from './thoughtDurationLabel';
import type { ThinkingEvent } from '../../store';
import { useAppStore } from '../../store';

afterEach(cleanup);

const events: ThinkingEvent[] = [
    {
        type: 'thought',
        text: 'Long reasoning that should collapse via the side rail.',
        timestamp: Date.now(),
    },
];

describe('ThinkingWidget side rail', () => {
    it('opens via header and collapses via the side rail', () => {
        render(
            <ThinkingWidget
                events={events}
                isActive={false}
                durationMs={5000}
            />
        );

        const content = document.querySelector('.thinking-content');
        expect(content?.classList.contains('is-open')).toBe(false);

        fireEvent.click(screen.getByTitle('Expand thoughts'));
        expect(content?.classList.contains('is-open')).toBe(true);

        const rail = screen.getByRole('button', { name: 'Collapse thoughts' });
        fireEvent.click(rail);
        expect(content?.classList.contains('is-open')).toBe(false);
    });

    it('auto-opens when mounted active and keeps the rail available', () => {
        render(
            <ThinkingWidget
                events={events}
                isActive={true}
                startTime={Date.now()}
            />
        );

        expect(document.querySelector('.thinking-content')?.classList.contains('is-open')).toBe(true);
        expect(screen.getByRole('button', { name: 'Collapse thoughts' })).toBeTruthy();
    });
});

describe('formatThoughtDurationLabel', () => {
    it('never invents "1 seconds" when duration is unknown', () => {
        expect(formatThoughtDurationLabel(undefined)).toBe('Thought');
        expect(formatThoughtDurationLabel(null)).toBe('Thought');
        expect(formatThoughtDurationLabel(0)).toBe('Thought');
    });

    it('uses honest pluralization and brief for sub-second', () => {
        expect(formatThoughtDurationLabel(200)).toBe('Thought briefly');
        expect(formatThoughtDurationLabel(1000)).toBe('Thought for 1 second');
        expect(formatThoughtDurationLabel(1500)).toBe('Thought for 2 seconds');
        expect(formatThoughtDurationLabel(17000)).toBe('Thought for 17 seconds');
    });

    it('renders Thought (not 1 seconds) when durationMs is missing on a completed widget', () => {
        render(
            <ThinkingWidget
                events={[{ type: 'thought', text: 'hmm', timestamp: Date.now() }]}
                isActive={false}
            />
        );
        expect(screen.getByText('Thought')).toBeTruthy();
        expect(screen.queryByText(/Thought for 1 seconds/i)).toBeNull();
    });
});

describe('addChatMessage thinkingDurationMs', () => {
    it('stamps end time on AI finalize when thinkingEndTime was never set', () => {
        const started = Date.now() - 12_000;
        useAppStore.setState({
            chatMessages: [],
            thinkingStartTime: started,
            thinkingEndTime: null,
            turnStartTime: started,
            streamTurn: null,
            activeTurn: null,
        });

        useAppStore.getState().addChatMessage('ai', 'done');
        const msg = useAppStore.getState().chatMessages[0];
        expect(msg.thinkingDurationMs).toBeTypeOf('number');
        expect(msg.thinkingDurationMs!).toBeGreaterThanOrEqual(11_000);
        // Live timers must not carry into the next turn — duration lives on the message.
        expect(useAppStore.getState().thinkingStartTime).toBeNull();
        expect(useAppStore.getState().thinkingEndTime).toBeNull();
    });

    it('suppresses research worker internal thoughts from rendering', () => {
        const mixedEvents: ThinkingEvent[] = [
            {
                type: 'thought',
                text: 'Deep research plan',
                agent_name: 'Deep Research',
                timestamp: Date.now(),
            },
            {
                type: 'thought',
                text: 'Worker scratchpad',
                agent_name: 'Research Worker 1',
                timestamp: Date.now(),
            },
            {
                type: 'thought',
                text: 'Another worker thought',
                agent_name: 'Research Worker 2',
                timestamp: Date.now(),
            },
        ];

        render(
            <ThinkingWidget
                events={mixedEvents}
                isActive={true}
            />
        );

        expect(screen.getByText(/Deep research plan/i)).toBeTruthy();
        expect(screen.queryByText(/Worker scratchpad/i)).toBeNull();
        expect(screen.queryByText(/Another worker thought/i)).toBeNull();
        expect(screen.queryByText(/Research Worker 1/i)).toBeNull();
    });

    it('renders Toolkit label and carousel when only toolMenu is present without events', () => {
        const toolMenu = {
            tools: [
                { name: 'search_web', description: 'Search the web', source: 'builtin' as const },
                { name: 'read_url', description: 'Read a URL', source: 'builtin' as const },
            ],
            shortlist_size: 2,
            selection_path: 'hybrid',
        };

        render(
            <ThinkingWidget
                events={[]}
                toolMenu={toolMenu}
                isActive={false}
            />
        );

        // Header shows 'Toolkit' label
        expect(screen.getByText('Toolkit')).toBeTruthy();

        // Expand widget
        fireEvent.click(screen.getByTitle('Expand thoughts'));

        // ToolMenuCarousel title and chips are rendered
        expect(screen.getByText(/Toolkit for this turn/i)).toBeTruthy();
        expect(screen.getByText('search web')).toBeTruthy();
        expect(screen.getByText('read url')).toBeTruthy();
    });
});

