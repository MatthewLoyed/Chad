/**
 * Voice-turn store slice (issue #654).
 * ====================================
 *
 * The behaviours that make voice mode observable in the GUI. The important
 * invariant is that voice state lives in its OWN slice: the chat turn lifecycle
 * pushes handleStateUpdate('ready') at several points, and voice feedback used
 * to ride that same channel, so unrelated chat activity silently wiped it.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from './store';
import type { VoiceAvailability } from './store';

const store = () => useAppStore.getState();

function resetStore() {
    useAppStore.setState({
        voiceTurn: null,
        voiceAvailability: null,
        status: 'idle',
    });
}

beforeEach(resetStore);

describe('voice turn phases', () => {
    it('keeps the transcript visible across later phases', () => {
        store().applyVoiceState({ phase: 'listening' });
        store().applyVoiceState({ phase: 'heard', transcript: 'turn on the lamp' });
        store().applyVoiceState({ phase: 'thinking' });

        // 'thinking' carries no transcript of its own; without carry-over the
        // pill would blank out mid-turn.
        expect(store().voiceTurn?.phase).toBe('thinking');
        expect(store().voiceTurn?.transcript).toBe('turn on the lamp');
    });

    it('accumulates the reply across streamed sentences', () => {
        store().applyVoiceState({ phase: 'listening' });
        store().applyVoiceState({ phase: 'speaking', reply: 'Turning it on.' });
        store().applyVoiceState({ phase: 'speaking', reply: 'Anything else?' });

        expect(store().voiceTurn?.reply).toBe('Turning it on. Anything else?');
    });

    it('clears the turn when the loop re-arms', () => {
        store().applyVoiceState({ phase: 'listening' });
        expect(store().voiceTurn).not.toBeNull();

        store().applyVoiceState({ phase: 'armed' });
        expect(store().voiceTurn).toBeNull();
    });

    it('starts a clean turn on the next wake', () => {
        store().applyVoiceState({ phase: 'listening' });
        store().applyVoiceState({ phase: 'heard', transcript: 'first command' });
        store().applyVoiceState({ phase: 'armed' });

        store().applyVoiceState({ phase: 'listening' });
        expect(store().voiceTurn?.transcript).toBeNull();
    });

    it('does not leak the previous transcript when wake fires mid-turn', () => {
        // Wake-word spam (issue #654 problem 1) can restart a turn without an
        // intervening 'armed'. The new turn must not inherit the old transcript.
        store().applyVoiceState({ phase: 'listening' });
        store().applyVoiceState({ phase: 'heard', transcript: 'first command' });
        store().applyVoiceState({ phase: 'listening' });

        expect(store().voiceTurn?.transcript).toBeNull();
        expect(store().voiceTurn?.reply).toBeNull();
    });

    it('surfaces the error text on a failed turn', () => {
        store().applyVoiceState({ phase: 'listening' });
        store().applyVoiceState({ phase: 'error', error: "Didn't hear anything" });

        expect(store().voiceTurn?.phase).toBe('error');
        expect(store().voiceTurn?.error).toBe("Didn't hear anything");
    });
});

describe('voice turn isolation from the chat status channel', () => {
    it('survives a chat turn ending', () => {
        store().applyVoiceState({ phase: 'thinking', transcript: 'turn on the lamp' });

        // What brainSocket does at every turn-end point.
        store().setStatus('ready');
        store().setStatusText('Ready');

        expect(store().voiceTurn?.phase).toBe('thinking');
        expect(store().voiceTurn?.transcript).toBe('turn on the lamp');
    });
});

describe('voice availability', () => {
    const failed: VoiceAvailability = {
        armed: false,
        reason: 'mic_unavailable',
        message: 'Microphone did not open',
        remedy: 'Set a default recording device.',
        detail: null,
    };

    it('starts null so "unknown" is distinguishable from "not armed"', () => {
        expect(store().voiceAvailability).toBeNull();
    });

    it('records the armed state', () => {
        store().setVoiceAvailability({ armed: true, reason: null, message: null, remedy: null, detail: null });
        expect(store().voiceAvailability?.armed).toBe(true);

        store().setVoiceAvailability(failed);
        expect(store().voiceAvailability?.armed).toBe(false);
    });

    it('retains the reason and the remedy for display', () => {
        store().setVoiceAvailability(failed);
        expect(store().voiceAvailability?.message).toBe('Microphone did not open');
        expect(store().voiceAvailability?.remedy).toBe('Set a default recording device.');
    });
});
