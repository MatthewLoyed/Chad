import { describe, expect, it } from 'vitest';
import { useAppStore, isStickyTurnPhaseLabel, isLiveTurnBlockingStatusPoll } from './store';
import type { TurnState } from './protocol/streamReducer';

describe('turn telemetry / statusText hygiene', () => {
    it('beginTurnWait clears prior tokenUsage so live speed cannot mix turns', () => {
        useAppStore.setState({
            tokenUsage: {
                prompt_tokens: 100,
                completion_tokens: 50,
                total_tokens: 150,
                speed: 42,
                ttft: 1.2,
            },
            liveStatus: null,
            thinkingStartTime: null,
        });

        useAppStore.getState().beginTurnWait();
        expect(useAppStore.getState().tokenUsage).toBeNull();
        expect(useAppStore.getState().liveStatus).toBeTruthy();
        expect(useAppStore.getState().thinkingStartTime).toBeTypeOf('number');
    });

    it('setStatus(ready) clears sticky Processing Prompt… even when already ready', () => {
        useAppStore.setState({
            status: 'ready',
            statusText: 'Processing Prompt...',
        });

        useAppStore.getState().setStatus('ready');
        expect(useAppStore.getState().statusText).toBe('');
    });

    it('setStatus(ready) from thinking clears sticky phase label', () => {
        useAppStore.setState({
            status: 'thinking',
            statusText: 'Processing Prompt...',
        });

        useAppStore.getState().setStatus('ready');
        expect(useAppStore.getState().status).toBe('ready');
        expect(useAppStore.getState().statusText).toBe('');
    });

    it('preserves abort / non-phase statusText on ready', () => {
        useAppStore.setState({
            status: 'thinking',
            statusText: 'Task stopped by user',
        });
        // abortActiveTurn sets both in one patch — simulate post-abort ready with that text
        useAppStore.setState({ status: 'ready', statusText: 'Task stopped by user' });
        useAppStore.getState().setStatus('ready');
        expect(useAppStore.getState().statusText).toBe('Task stopped by user');
    });

    it('isStickyTurnPhaseLabel only matches known turn-phase copy', () => {
        expect(isStickyTurnPhaseLabel('Processing Prompt...')).toBe(true);
        expect(isStickyTurnPhaseLabel('Model Loading...')).toBe(true);
        expect(isStickyTurnPhaseLabel('Task stopped by user')).toBe(false);
        expect(isStickyTurnPhaseLabel("Say \"Hey Chad\"")).toBe(false);
        expect(isStickyTurnPhaseLabel('')).toBe(false);
    });

    it('idle status poll only blocks on a running streamTurn, not a finished zombie', () => {
        const finished: TurnState = {
            turn_id: 't1',
            lifecycle: 'finished',
            parts: [],
            usage: null,
            response: null,
            error: null,
            lastSeq: 1,
            seqGap: false,
        };
        expect(isLiveTurnBlockingStatusPoll({
            streamTurn: finished,
            activeTurn: null,
            streamingResponse: null,
        })).toBe(false);

        expect(isLiveTurnBlockingStatusPoll({
            streamTurn: { ...finished, lifecycle: 'running' },
            activeTurn: null,
            streamingResponse: null,
        })).toBe(true);
    });

    it('AI finalize clears live timers and flips busy → ready', () => {
        const started = Date.now() - 5_000;
        useAppStore.setState({
            chatMessages: [],
            status: 'thinking',
            statusText: 'Processing Prompt...',
            thinkingStartTime: started,
            thinkingEndTime: null,
            turnStartTime: started,
            streamTurn: {
                turn_id: 't-fin',
                lifecycle: 'finished',
                parts: [],
                usage: null,
                response: null,
                error: null,
                lastSeq: 1,
                seqGap: false,
            },
            streamConversationId: 'conv-1',
            activeTurn: null,
            streamingResponse: null,
        });

        useAppStore.getState().addChatMessage('ai', 'done');
        const after = useAppStore.getState();
        expect(after.status).toBe('ready');
        expect(after.statusText).toBe('');
        expect(after.streamTurn).toBeNull();
        expect(after.thinkingStartTime).toBeNull();
        expect(after.thinkingEndTime).toBeNull();
        expect(after.chatMessages[0].thinkingDurationMs).toBeGreaterThanOrEqual(4_000);
    });
});
