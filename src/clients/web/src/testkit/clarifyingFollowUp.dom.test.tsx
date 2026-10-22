// @vitest-environment jsdom
/**
 * GUI-symptom reproduction: "the clarifying questions show up AGAIN on my follow-up".
 *
 * Reported from the desktop GUI. This replays the message sequence through the real
 * <ChatStream/> in jsdom — no Brain, no model — to localise it: if the card duplicates
 * here, it is a Limb rendering bug; if it doesn't, Chad is sending odd message state.
 *
 * The mechanism under test:
 *   ChatStream renders a clarifying card for a USER message either from its own
 *   `clarifyingReply`, or inferred via `clarifyingReplyFromPrior(prevMsg, msg)` when the
 *   PRECEDING message is a resolved questions approval. `prevMsg` is taken from
 *   `displayMessages` — which filters out in-progress AI messages while a turn streams.
 *   So while the follow-up turn is live, the AI message that should separate the answer
 *   from the follow-up disappears, the follow-up becomes adjacent to the old questions
 *   message, and the inference fires a second time on a message that never answered it.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import ChatStream from '../components/layout/ChatStream';
import { useAppStore, type ChatMessage } from '../store';
import { REPLAY_CONVERSATION_ID } from './domReplay';

afterEach(cleanup);

const QUESTIONS = [
    {
        question: 'How much of this should I implement now?',
        header: 'Scope',
        options: [{ label: 'Frontend only' }, { label: 'Everything' }],
    },
];

function installShims(): void {
    if (!Element.prototype.scrollIntoView) {
        Element.prototype.scrollIntoView = function () { /* no-op */ };
    }
    if (typeof window.matchMedia !== 'function') {
        window.matchMedia = ((q: string) => ({
            matches: false, media: q, onchange: null,
            addEventListener: () => {}, removeEventListener: () => {},
            addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
        })) as unknown as typeof window.matchMedia;
    }
    if (typeof window.ResizeObserver !== 'function') {
        window.ResizeObserver = class {
            observe() {} unobserve() {} disconnect() {}
        } as unknown as typeof window.ResizeObserver;
    }
}

const at = (min: number) => new Date(2026, 6, 24, 12, min);

/** Chad asked clarifying questions and the user answered them — the resolved card. */
const askedQuestions: ChatMessage = {
    id: 'ai-questions',
    role: 'ai',
    text: '',
    timestamp: at(0),
    actions: {
        type: 'tool_approval',
        token: 'tok-1',
        decisionType: 'questions',
        prompt: 'A couple of questions before I start',
        questions: QUESTIONS,
        resolved: true,
        userChoice: JSON.stringify({
            answers: { 'How much of this should I implement now?': 'Frontend only' },
        }),
    },
};

/** The user's answer to those questions. Correctly renders the Q&A card. */
const answer: ChatMessage = {
    id: 'user-answer', role: 'user', text: 'Frontend only', timestamp: at(1),
};

/** Chad's reply to the answer. Still streaming when the follow-up is sent. */
const replyInProgress: ChatMessage = {
    id: 'ai-reply', role: 'ai', text: 'Got it — starting on the frontend.',
    timestamp: at(2), in_progress: true,
};

/** A brand-new, unrelated follow-up. Must NOT show the earlier questions. */
const followUp: ChatMessage = {
    id: 'user-followup', role: 'user', text: 'actually also add dark mode', timestamp: at(3),
};

function renderWith(messages: ChatMessage[], liveTurn: boolean) {
    installShims();
    useAppStore.getState().clearThinkingEvents();
    useAppStore.setState({
        chatMessages: messages,
        tokenUsage: null,
        status: liveTurn ? 'thinking' : 'ready',
        // A live streamTurn is what makes ChatStream filter in-progress AI messages.
        streamTurn: liveTurn
            ? ({ lifecycle: 'running', parts: [], order: [] } as never)
            : null,
        activeConversationId: REPLAY_CONVERSATION_ID,
        streamConversationId: REPLAY_CONVERSATION_ID,
    });
    return render(<ChatStream />);
}

/** How many clarifying Q&A cards are on screen. */
function clarifyingCardCount(container: HTMLElement): number {
    return container.querySelectorAll('.clarify-card--resolved').length;
}

describe('clarifying questions — follow-up duplication', () => {
    it('renders exactly one card for the answer itself (baseline)', () => {
        const { container } = renderWith([askedQuestions, answer], false);
        expect(clarifyingCardCount(container)).toBe(1);
    });

    it('does not re-render the card on a later follow-up (turn settled)', () => {
        const { container } = renderWith(
            [askedQuestions, answer, { ...replyInProgress, in_progress: false }, followUp],
            false,
        );
        expect(clarifyingCardCount(container)).toBe(1);
    });

    it('follow-up sent while the reply is still streaming', () => {
        // The in-progress AI reply is filtered from displayMessages, but the user's
        // answer still separates the questions from the follow-up, so the inference
        // correctly declines. (First hypothesis — did NOT reproduce.)
        const { container } = renderWith(
            [askedQuestions, answer, replyInProgress, followUp],
            true,
        );
        expect(clarifyingCardCount(container)).toBe(1);
    });

    it('THE REPORTED BUG: card resolved in place, no separate answer message', () => {
        // When the user answers by clicking inside the card, the choice is recorded on
        // the AI message's `actions.userChoice` and there may be no separate user
        // message. The next thing the user types is then directly adjacent to the
        // resolved questions, so clarifyingReplyFromPrior fires on a message that never
        // answered anything — the follow-up wears the old questions.
        const { container } = renderWith([askedQuestions, followUp], false);
        expect(clarifyingCardCount(container)).toBe(0);
    });
});
