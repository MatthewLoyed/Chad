// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import ClarifyingQuestionsCard from './ClarifyingQuestionsCard';
import {
    parseResolvedQaPairs,
    type ClarifyingQuestion,
} from './clarifying-questions-utils';

afterEach(cleanup);

const QUESTIONS: ClarifyingQuestion[] = [
    {
        question: 'How much of this should I implement now?',
        header: 'Scope',
        options: [
            { label: 'Full fix (frontend + backend)' },
            { label: 'Frontend only' },
        ],
    },
];

describe('parseResolvedQaPairs', () => {
    it('splits structured answers into question + answer pairs', () => {
        const choice = JSON.stringify({
            answers: {
                'How much of this should I implement now?': 'Full fix (frontend + backend)',
            },
        });
        expect(parseResolvedQaPairs(QUESTIONS, choice)).toEqual([
            {
                question: 'How much of this should I implement now?',
                answer: 'Full fix (frontend + backend)',
            },
        ]);
    });

    it('falls back to plain choice text when JSON is absent', () => {
        expect(parseResolvedQaPairs(QUESTIONS, 'Frontend only')).toEqual([
            {
                question: 'How much of this should I implement now?',
                answer: 'Frontend only',
            },
        ]);
    });
});

describe('ClarifyingQuestionsCard resolved UI', () => {
    it('renders muted question above bold answer (Claude-style)', () => {
        const choice = JSON.stringify({
            answers: {
                'How much of this should I implement now?': 'Full fix (frontend + backend)',
            },
        });
        const { container } = render(
            <ClarifyingQuestionsCard
                questions={QUESTIONS}
                resolved
                userChoice={choice}
            />,
        );

        expect(container.querySelector('.clarify-card__qa-question')?.textContent).toBe(
            'How much of this should I implement now?',
        );
        expect(container.querySelector('.clarify-card__qa-answer')?.textContent).toBe(
            'Full fix (frontend + backend)',
        );
        expect(screen.queryByText('A few quick questions')).toBeNull();
        expect(screen.queryByText('Quick question')).toBeNull();
    });
});

describe('ClarifyingQuestionsCard interactive UI', () => {
    it('uses numbered options and something-else footer for single-select', () => {
        const { container } = render(<ClarifyingQuestionsCard questions={QUESTIONS} />);
        expect(screen.getByRole('radio', { name: /Full fix/i })).toBeTruthy();
        expect(screen.getByText('Something else')).toBeTruthy();
        expect(container.querySelector('.clarify-card__option-marker--num')?.textContent).toBe('1');
        expect(screen.queryByText('Select all that apply')).toBeNull();
    });

    it('shows select-all hint and checkboxes when multiSelect is true', () => {
        const multi: ClarifyingQuestion[] = [
            {
                question: 'Any specific activities or amenities you must have?',
                header: 'Amenities',
                multiSelect: true,
                options: [
                    { label: 'Beach relaxation', description: 'Quiet spa focus' },
                    { label: 'Water sports', description: 'Snorkel and dive' },
                    { label: 'Romantic dining', description: 'Fine dining' },
                ],
            },
        ];
        render(<ClarifyingQuestionsCard questions={multi} />);
        expect(screen.getByText('Select all that apply')).toBeTruthy();
        expect(screen.getByRole('checkbox', { name: /Beach relaxation/i })).toBeTruthy();
        expect(screen.getByRole('checkbox', { name: /Water sports/i })).toBeTruthy();
    });
});
