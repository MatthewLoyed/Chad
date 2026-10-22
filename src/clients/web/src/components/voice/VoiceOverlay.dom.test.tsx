// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, act } from '@testing-library/react';
import VoiceOverlay from './VoiceOverlay';
import { useAppStore } from '../../store';

function resetStore() {
    useAppStore.setState({
        voiceTurn: null,
        voiceAvailability: null,
    });
}

beforeEach(() => {
    resetStore();
    vi.useFakeTimers();
});

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    resetStore();
});

describe('VoiceOverlay unavailable banner', () => {
    it('does NOT render when voice mode is simply disabled', () => {
        useAppStore.setState({
            voiceAvailability: {
                armed: false,
                reason: 'disabled',
                message: 'Voice mode is off',
                remedy: 'Turn it on in Settings -> Audio & Voice.',
                detail: null,
            },
        });

        const { container } = render(<VoiceOverlay />);
        expect(container.querySelector('.voice-unavailable')).toBeNull();
        expect(screen.queryByText('Voice mode is off')).toBeNull();
    });

    it('does NOT render when voice mode is armed', () => {
        useAppStore.setState({
            voiceAvailability: {
                armed: true,
                reason: null,
                message: null,
                remedy: null,
                detail: null,
            },
        });

        const { container } = render(<VoiceOverlay />);
        expect(container.querySelector('.voice-unavailable')).toBeNull();
    });

    it('renders when voice mode encountered a genuine failure, and allows manual dismissal', () => {
        useAppStore.setState({
            voiceAvailability: {
                armed: false,
                reason: 'mic_unavailable',
                message: 'Microphone did not open',
                remedy: 'Set a default recording device.',
                detail: null,
            },
        });

        const { container } = render(<VoiceOverlay />);
        expect(container.querySelector('.voice-unavailable')).not.toBeNull();
        expect(screen.getByText('Microphone did not open')).not.toBeNull();
        expect(screen.getByText('Set a default recording device.')).not.toBeNull();

        // Dismiss via close button triggers exit animation
        const closeBtn = screen.getByRole('button', { name: 'Dismiss voice status' });
        act(() => {
            fireEvent.click(closeBtn);
        });

        // Framer motion sets opacity: 0 for the exit animation
        const chip = container.querySelector('.voice-unavailable');
        expect(chip?.getAttribute('style')).toContain('opacity: 0');
    });

    it('auto-dismisses after timeout', () => {
        useAppStore.setState({
            voiceAvailability: {
                armed: false,
                reason: 'wake_word_unavailable',
                message: 'Wake word engine unavailable',
                remedy: 'Install voice deps',
                detail: null,
            },
        });

        const { container } = render(<VoiceOverlay />);
        expect(container.querySelector('.voice-unavailable')).not.toBeNull();

        act(() => {
            vi.advanceTimersByTime(8000);
        });

        const chip = container.querySelector('.voice-unavailable');
        expect(chip?.getAttribute('style')).toContain('opacity: 0');
    });

    it('calls onOpenSettings and dismisses when clicking remedy', () => {
        const onOpenSettings = vi.fn();
        useAppStore.setState({
            voiceAvailability: {
                armed: false,
                reason: 'stt_unavailable',
                message: 'Speech-to-text unavailable',
                remedy: 'Install faster-whisper',
                detail: null,
            },
        });

        const { container } = render(<VoiceOverlay onOpenSettings={onOpenSettings} />);
        const remedy = screen.getByText('Install faster-whisper');
        act(() => {
            fireEvent.click(remedy);
        });

        expect(onOpenSettings).toHaveBeenCalledTimes(1);
        const chip = container.querySelector('.voice-unavailable');
        expect(chip?.getAttribute('style')).toContain('opacity: 0');
    });
});
