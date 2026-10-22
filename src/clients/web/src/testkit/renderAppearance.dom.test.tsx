// @vitest-environment jsdom
/**
 * Appearance probes: plan cards, the sources footer, and subagent/model attribution.
 *
 * Same approach as clarifyingFollowUp — drive the real <ChatStream/> in jsdom from
 * synthetic state so a reported "it looks wrong" can be localised to the Limb or the
 * Brain without running Chad. See .claude/skills/gui-bug-repro/SKILL.md.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import ChatStream from '../components/layout/ChatStream';
import { useAppStore, type ChatMessage } from '../store';
import { REPLAY_CONVERSATION_ID } from './domReplay';

afterEach(cleanup);

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

function renderWith(messages: ChatMessage[], liveTurn: boolean) {
    installShims();
    useAppStore.getState().clearThinkingEvents();
    useAppStore.setState({
        chatMessages: messages,
        tokenUsage: null,
        status: liveTurn ? 'thinking' : 'ready',
        streamTurn: liveTurn
            ? ({ lifecycle: 'running', parts: [], order: [] } as never)
            : null,
        activeConversationId: REPLAY_CONVERSATION_ID,
        streamConversationId: REPLAY_CONVERSATION_ID,
    });
    return render(<ChatStream />);
}

const planMessage = (id: string, resolved: boolean): ChatMessage => ({
    id,
    role: 'ai',
    text: JSON.stringify({
        steps: [
            { description: 'Read the config', status: 'completed' },
            { description: 'Apply the change', status: 'pending' },
        ],
    }),
    timestamp: at(1),
    actions: { type: 'file_plan_approval', approvalToken: 'tok', resolved },
});

const userMsg = (id: string, text: string, min: number): ChatMessage => ({
    id, role: 'user', text, timestamp: at(min),
});

describe('plan card appearance', () => {
    it('renders one plan card for a plan message (baseline)', () => {
        const { container } = renderWith([userMsg('u1', 'do the thing', 0), planMessage('p1', false)], false);
        expect(container.querySelectorAll('.plan-card').length).toBe(1);
    });

    it('the current plan renders EXPANDED (control)', () => {
        // `isHistory` emits no class — it seeds `useState(!isHistory)`, so the only
        // observable is whether the card is expanded. Asserting on a `--history` class
        // would pass vacuously. With no filtering, idx and messages.length agree.
        const { container } = renderWith([userMsg('u1', 'do the thing', 0), planMessage('p1', false)], false);
        expect(container.querySelectorAll('.plan-card.is-expanded').length).toBe(1);
    });

    it('BUG PROBE: live turn filters a message, so the plan card indexes against the wrong length', () => {
        // `isHistory` is `idx < messages.length - 1`, where `idx` indexes displayMessages
        // but `messages` is the UNFILTERED list. While a turn streams, displayMessages
        // drops in-progress AI messages and is SHORTER, so the last plan card's idx is
        // always < unfiltered length - 1 — a current plan collapses as though historical.
        const streamingReply: ChatMessage = {
            id: 'ai-live', role: 'ai', text: 'working on it', timestamp: at(2), in_progress: true,
        };
        const { container } = renderWith(
            [userMsg('u1', 'do the thing', 0), planMessage('p1', false), streamingReply],
            true,
        );
        expect(container.querySelectorAll('.plan-card.is-expanded').length).toBe(1);
    });
});

describe('thinking dropdown — subagent / model attribution', () => {
    const withSubagentThoughts: ChatMessage = {
        id: 'ai-thoughts',
        role: 'ai',
        text: 'Done.',
        timestamp: at(1),
        thoughts: [
            { type: 'thought', text: 'Deciding how to research this.', agent_name: 'Supervisor' },
            { type: 'thought', text: 'Digging through the results.', agent_name: 'Deep Research Agent' },
        ] as never,
    };

    it('attributes each thought block to its agent', () => {
        const { container } = renderWith([userMsg('u1', 'research X', 0), withSubagentThoughts], false);
        const text = container.textContent ?? '';
        expect(text).toContain('Supervisor');
        expect(text).toContain('Deep Research Agent');
    });

    it('does not merge thoughts from different agents into one block', () => {
        // ThinkingWidget merges consecutive mergeable events only when agent_name matches,
        // so two agents must stay two bubbles. Merging them would misattribute a
        // subagent's reasoning to the supervisor.
        const { container } = renderWith([userMsg('u1', 'research X', 0), withSubagentThoughts], false);
        expect(container.querySelectorAll('.thinking-bubble-wrapper').length).toBeGreaterThanOrEqual(2);
    });
});

describe('sources footer appearance', () => {
    const withSources: ChatMessage = {
        id: 'ai-sources',
        role: 'ai',
        text: 'Here is what I found [1].',
        timestamp: at(1),
        sources: [
            { title: 'Example source', url: 'https://example.com' },
        ] as never,
    };

    it('renders a single sources footer for a finished research turn', () => {
        const { container } = renderWith([userMsg('u1', 'research X', 0), withSources], false);
        expect(container.querySelectorAll('.sources-footer').length).toBe(1);
    });

    it('BUG PROBE: no empty footer chrome when a turn has no sources', () => {
        const plain: ChatMessage = { id: 'ai-plain', role: 'ai', text: 'No research needed.', timestamp: at(1) };
        const { container } = renderWith([userMsg('u1', 'hi', 0), plain], false);
        expect(container.querySelectorAll('.sources-footer').length).toBe(0);
    });

    it('BUG PROBE: history footer and live footer do not both render mid-turn', () => {
        // ChatStream renders <SourcesFooter sources={msg.sources}/> per AI message AND
        // <SourcesFooter sources={liveSources}/> for the active turn. If a finished
        // sourced message is still on screen while a new turn runs, both could show.
        const streamingReply: ChatMessage = {
            id: 'ai-live', role: 'ai', text: '', timestamp: at(2), in_progress: true,
        };
        const { container } = renderWith(
            [userMsg('u1', 'research X', 0), withSources, streamingReply],
            true,
        );
        expect(container.querySelectorAll('.sources-footer').length).toBe(1);
    });
});
