import { describe, expect, it } from 'vitest';
import { resolveBuddyMode } from './resolveBuddyMode';

const base = {
    streamTurn: null,
    streamConversationId: null,
    activeConversationId: 'conv-a',
    status: 'ready',
    voicePhase: null,
};

describe('resolveBuddyMode', () => {
    it('returns idle when stream belongs to a different conversation', () => {
        expect(
            resolveBuddyMode({
                ...base,
                streamConversationId: 'conv-b',
                streamTurn: { lifecycle: 'running', parts: [] },
                status: 'thinking',
            }),
        ).toBe('idle');
    });

    it('returns thinking for a scoped running turn', () => {
        expect(
            resolveBuddyMode({
                ...base,
                streamConversationId: 'conv-a',
                streamTurn: { lifecycle: 'running', parts: [{ kind: 'reasoning' }] },
                status: 'thinking',
            }),
        ).toBe('thinking');
    });

    it('returns working when scoped turn has an open tool', () => {
        expect(
            resolveBuddyMode({
                ...base,
                streamConversationId: 'conv-a',
                streamTurn: {
                    lifecycle: 'running',
                    parts: [{ kind: 'tool', ok: undefined }],
                },
                status: 'executing',
            }),
        ).toBe('working');
    });

    it('ignores global thinking status when not scoped to active chat', () => {
        expect(
            resolveBuddyMode({
                ...base,
                streamConversationId: 'conv-b',
                status: 'thinking',
            }),
        ).toBe('idle');
    });

    // A "Hey Chad" turn belongs to no conversation, so it must bypass the
    // conversation guard entirely — otherwise the buddy sits idle through the
    // whole voice interaction (issue #654).
    it.each([
        ['listening', 'listening'],
        ['transcribing', 'thinking'],
        ['heard', 'thinking'],
        ['thinking', 'thinking'],
        ['speaking', 'speaking'],
        ['error', 'error'],
    ])('maps voice phase %s to buddy mode %s', (phase, expected) => {
        expect(resolveBuddyMode({ ...base, voicePhase: phase })).toBe(expected);
    });

    it('drives the buddy even while another conversation owns the stream', () => {
        expect(
            resolveBuddyMode({
                ...base,
                streamConversationId: 'conv-b',
                voicePhase: 'listening',
            }),
        ).toBe('listening');
    });

    it('falls through to chat state when voice is merely armed', () => {
        expect(
            resolveBuddyMode({
                ...base,
                streamConversationId: 'conv-a',
                streamTurn: { lifecycle: 'running', parts: [] },
                voicePhase: 'armed',
            }),
        ).toBe('thinking');
    });
});
