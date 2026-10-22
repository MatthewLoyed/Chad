import { describe, expect, it } from 'vitest';
import { applyStreamEvent, initialTurnState } from '../protocol/streamReducer';
import { applyToolApprovalFollowUp } from './tool-approval-followup';
import { getActiveChatStreamTurn } from '../store';

function baseState() {
    return {
        chatMessages: [] as ReturnType<typeof applyToolApprovalFollowUp>['chatMessages'],
        streamTurn: null,
        streamConversationId: 'conv-1',
        pendingToolApproval: {
            token: 'CHAD-TEST',
            tool_name: 'ask_user_question',
            decision_type: 'questions' as const,
            prompt: 'Pick one',
            title: 'Quick question',
            questions: [
                {
                    question: 'Which?',
                    options: [{ label: 'A' }, { label: 'B' }],
                },
            ],
        },
        skipNextAiTurnFinalizeId: null,
        turnStartTime: undefined,
        thinkingStartTime: null,
        thinkingEndTime: null,
        liveStatus: null,
        activeTurn: null,
    };
}

describe('applyToolApprovalFollowUp', () => {
    it('commits in-flight streamTurn before follow-up run_started would wipe it', () => {
        let turn = initialTurnState();
        turn = applyStreamEvent(turn, { type: 'run_started', turn_id: 'turn-abc', seq: 1 });
        turn = applyStreamEvent(turn, {
            type: 'reasoning_delta',
            part_id: 'r1',
            delta: 'Planning…',
            seq: 2,
            turn_id: 'turn-abc',
        });
        turn = applyStreamEvent(turn, {
            type: 'tool_start',
            tool_call_id: 't1',
            name: 'ask_user_question',
            args: { questions: [] },
            display_text: 'Asking clarifying questions',
            seq: 3,
            turn_id: 'turn-abc',
        });

        const next = applyToolApprovalFollowUp(
            { ...baseState(), streamTurn: turn },
            '{"answers":{"Which?":"A"}}',
            'A',
        );

        expect(next.streamTurn).toBeNull();
        expect(next.pendingToolApproval).toBeNull();
        expect(next.skipNextAiTurnFinalizeId).toBe('turn-abc');
        expect(next.chatMessages).toHaveLength(2);

        const aiMsg = next.chatMessages[0];
        expect(aiMsg.role).toBe('ai');
        expect(aiMsg.thoughts?.length).toBeGreaterThan(0);
        expect(aiMsg.actions?.type).toBe('tool_approval');
        expect(aiMsg.actions?.resolved).toBe(true);
        expect(aiMsg.actions?.userChoice).toBe('{"answers":{"Which?":"A"}}');

        const userMsg = next.chatMessages[1];
        expect(userMsg.role).toBe('user');
        expect(userMsg.text).toBe('A');
        expect(userMsg.clarifyingReply?.userChoice).toBe('{"answers":{"Which?":"A"}}');
    });

    it('marks an already-committed approval card resolved when streamTurn is empty', () => {
        const state = {
            ...baseState(),
            pendingToolApproval: {
                token: 'CHAD-TEST',
                tool_name: 'ask_user_question',
                decision_type: 'questions' as const,
                prompt: 'Pick one',
                title: 'Quick question',
                questions: [
                    {
                        question: 'Which?',
                        options: [{ label: 'A' }, { label: 'B' }],
                    },
                ],
            },
            chatMessages: [{
                id: 'ai-1',
                role: 'ai' as const,
                text: '',
                timestamp: new Date(),
                thoughts: [{ type: 'tool_start' as const, name: 'ask_user_question' }],
                actions: {
                    type: 'tool_approval' as const,
                    token: 'CHAD-TEST',
                    decisionType: 'questions' as const,
                    prompt: 'Pick one',
                    questions: [
                        {
                            question: 'Which?',
                            options: [{ label: 'A' }, { label: 'B' }],
                        },
                    ],
                    resolved: false,
                },
            }],
        };

        const next = applyToolApprovalFollowUp(state, '{"answers":{"Which?":"A"}}', 'A');

        expect(next.chatMessages).toHaveLength(2);
        expect(next.chatMessages[0].actions?.resolved).toBe(true);
        expect(next.chatMessages[0].thoughts?.length).toBe(1);
        expect(next.chatMessages[1].text).toBe('A');
        expect(next.chatMessages[1].clarifyingReply?.questions[0].question).toBe('Which?');
    });
});


it('keeps desktop approval continuation thoughts visible in the active conversation', () => {
    const next = applyToolApprovalFollowUp({ ...baseState(), activeConversationId: 'conv-1' }, 'approved');
    const streamTurn = applyStreamEvent(initialTurnState(), {
        type: 'run_started', turn_id: 'approval-followup', seq: 1,
    });
    const resumed = applyStreamEvent(streamTurn, {
        type: 'reasoning_delta', turn_id: 'approval-followup', seq: 2, part_id: 'receipt', delta: 'Reading tool output',
    });
    expect(next.streamConversationId).toBe('conv-1');
    expect(getActiveChatStreamTurn({ ...next, activeConversationId: 'conv-1', streamTurn: resumed })).toBe(resumed);
    expect(getActiveChatStreamTurn({ ...next, activeConversationId: 'other-chat', streamTurn: resumed })).toBeNull();
});

it('restores approval continuation ownership from active chat when finalized history has no stream owner', () => {
    const next = applyToolApprovalFollowUp({ ...baseState(), activeConversationId: 'conv-1', streamConversationId: null }, 'approved');
    expect(next.streamConversationId).toBe('conv-1');
});
