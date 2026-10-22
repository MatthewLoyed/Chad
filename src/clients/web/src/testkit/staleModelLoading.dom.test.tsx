// @vitest-environment jsdom
/**
 * GUI-symptom reproduction: "Loading … from lmstudio" stays on screen after Gemma
 * is already loaded / the turn has moved on (toolkit visible, wait-theater running).
 *
 * The Loading line is gated on AppStatus === 'model_loading', which App.tsx sets
 * optimistically on every local send. It is NOT cleared by LM Studio reporting the
 * model resident — only by a later status flip. When that flip is missed (or never
 * sent), the indicator sticks while tool_menu / liveStatus keep progressing.
 *
 * Correct behaviour: once the live turn has progressed past model load (toolkit,
 * reasoning, tools), the Loading indicator must not show — even if status is still
 * stuck at model_loading.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import ChatStream from '../components/layout/ChatStream';
import { useAppStore } from '../store';
import { applyStreamEvent, initialTurnState, type TurnState } from '../protocol/streamReducer';
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

function runningTurn(): TurnState {
    return applyStreamEvent(initialTurnState(), { type: 'run_started', turn_id: 't1', seq: 0 });
}

function renderLive(opts: {
    status: 'model_loading' | 'thinking';
    turn: TurnState | null;
    liveStatus?: string | null;
}) {
    installShims();
    useAppStore.getState().clearThinkingEvents();
    useAppStore.setState({
        chatMessages: [],
        tokenUsage: null,
        status: opts.status,
        streamTurn: opts.turn,
        liveStatus: opts.liveStatus ?? null,
        liveStatusUpdatedAt: opts.liveStatus ? Date.now() : null,
        activeModel: {
            mode: 'local',
            name: 'google/gemma-4-26b-a4b-qat',
            provider: 'lmstudio',
            supports_tools: true,
            thinking_enabled: null,
            model_settings: {},
        },
        activeConversationId: REPLAY_CONVERSATION_ID,
        streamConversationId: REPLAY_CONVERSATION_ID,
        thinkingStartTime: Date.now(),
    });
    return render(<ChatStream />);
}

describe('stale model_loading indicator', () => {
    it('shows Loading… while status is model_loading and the turn has not progressed (baseline)', () => {
        const { container } = renderLive({
            status: 'model_loading',
            turn: runningTurn(),
        });
        const text = container.textContent || '';
        expect(text).toMatch(/Loading google\/gemma-4-26b-a4b-qat from lmstudio/i);
    });

    it('does NOT show Loading… once the toolkit is on screen, even if status is stuck at model_loading', () => {
        let turn = runningTurn();
        turn = applyStreamEvent(turn, {
            type: 'tools_snapshot',
            turn_id: 't1',
            seq: 1,
            menu_id: 't1-tools',
            tools: ['web_search', 'call_mcp_tool'],
            shortlist_size: 8,
            selection_path: 'llm',
        });

        const { container } = renderLive({
            status: 'model_loading',
            turn,
            liveStatus: 'Working through it…',
        });
        const text = container.textContent || '';
        expect(text).not.toMatch(/Loading google\/gemma-4-26b-a4b-qat from lmstudio/i);
        expect(text).toMatch(/Working through it|TOOLKIT|web search|call mcp/i);
    });

    it('hides Loading… when status has flipped to thinking (control)', () => {
        const { container } = renderLive({
            status: 'thinking',
            turn: runningTurn(),
            liveStatus: 'Working through it…',
        });
        expect(container.textContent || '').not.toMatch(
            /Loading google\/gemma-4-26b-a4b-qat from lmstudio/i,
        );
    });
});
