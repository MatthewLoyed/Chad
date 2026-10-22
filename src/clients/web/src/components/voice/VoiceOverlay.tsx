/**
 * Voice turn overlay — the GUI feedback for "Hey Chad" (issue #654).
 *
 * Three pieces, all driven by the dedicated voiceTurn / voiceAvailability store
 * slices (never the shared status channel, which chat activity overwrites):
 *
 *   1. Ambient glow   — an animated border around the window for the turn's duration.
 *   2. Voice pill     — top-center, shows the phase and, critically, the transcript
 *                       Chad actually heard. Click to expand for the reply.
 *   3. Unavailable chip — bottom-left, only when voice mode failed to arm, with
 *                       the reason and the fix.
 *
 * Deliberately NOT wired into the chat: a voice turn is a quick command, not a
 * conversation, so it never writes to the active chat's message list.
 */
import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Mic, MicOff, Loader2, Volume2, AlertTriangle, X } from 'lucide-react';
import { useVoiceTurn, useVoiceAvailability } from '../../store';
import type { VoicePhase } from '../../store';
import './VoiceOverlay.css';

export interface VoiceOverlayProps {
    onOpenSettings?: () => void;
}

/**
 * Minimum time the overlay stays mounted once it appears. The wake word can
 * currently re-fire in bursts (issue #654 problem 1); without a floor the pill
 * strobes on and off and becomes unreadable — and unreadable feedback is worse
 * than none for diagnosing that very bug.
 */
const MIN_VISIBLE_MS = 900;
/** How long a finished turn lingers before fading, so you can read the result. */
const LINGER_MS = 2200;
/** Errors linger longer — they're the thing you actually need to read. */
const ERROR_LINGER_MS = 5000;

const PHASE_LABEL: Record<VoicePhase, string> = {
    armed: 'Listening for "Hey Chad"',
    listening: 'Listening…',
    transcribing: 'Getting that…',
    heard: 'Heard you',
    thinking: 'Thinking…',
    speaking: 'Speaking',
    error: "Didn't work",
};

function PhaseIcon({ phase }: { phase: VoicePhase }) {
    if (phase === 'error') return <AlertTriangle size={14} />;
    if (phase === 'speaking') return <Volume2 size={14} />;
    if (phase === 'thinking' || phase === 'transcribing') {
        return <Loader2 size={14} className="voice-pill__spin" />;
    }
    return <Mic size={14} />;
}

export default function VoiceOverlay({ onOpenSettings }: VoiceOverlayProps = {}) {
    const voiceTurn = useVoiceTurn();
    const availability = useVoiceAvailability();
    const [expanded, setExpanded] = useState(false);
    const [dismissed, setDismissed] = useState(false);
    const lastReason = useRef<string | null>(null);

    // The turn we're actually rendering. Lags the store on the way *down* only:
    // the controller emits 'armed' the instant a turn ends (which nulls the
    // store), so without this the result would vanish before it could be read.
    const [shown, setShown] = useState(voiceTurn);
    const appearedAt = useRef<number>(0);

    // Reset dismissed state whenever a new, distinct failure arrives
    useEffect(() => {
        if (availability && !availability.armed && availability.reason !== 'disabled') {
            if (availability.reason !== lastReason.current) {
                lastReason.current = availability.reason;
                const frame = requestAnimationFrame(() => setDismissed(false));
                return () => cancelAnimationFrame(frame);
            }
        } else {
            lastReason.current = null;
        }
    }, [availability]);

    // Auto-dismiss after 8 seconds so the error chip is never a permanent banner
    useEffect(() => {
        if (!availability || availability.armed || availability.reason === 'disabled' || dismissed) {
            return;
        }
        const timer = setTimeout(() => {
            setDismissed(true);
        }, 8000);
        return () => clearTimeout(timer);
    }, [availability, dismissed]);

    useEffect(() => {
        if (voiceTurn) {
            if (!shown) appearedAt.current = Date.now();
            const frame = requestAnimationFrame(() => setShown(voiceTurn));
            return () => cancelAnimationFrame(frame);
        }
        if (!shown) return;

        // Turn ended. Hold for the linger window, but never cut short of the
        // anti-strobe floor.
        const linger = shown.phase === 'error' ? ERROR_LINGER_MS : LINGER_MS;
        const elapsed = Date.now() - appearedAt.current;
        const wait = Math.max(linger, MIN_VISIBLE_MS - elapsed);
        const timer = setTimeout(() => {
            setShown(null);
            setExpanded(false);
        }, wait);
        return () => clearTimeout(timer);
    }, [voiceTurn, shown]);

    const phase = shown?.phase;
    // Never show an unavailable banner when voice mode is simply disabled/off.
    // Only surface genuine failures (hardware/model/runtime), and allow dismissal.
    const showUnavailable =
        availability !== null &&
        !availability.armed &&
        availability.reason !== 'disabled' &&
        !dismissed;

    return (
        <>
            <AnimatePresence>
                {phase && (
                    <motion.div
                        key="voice-glow"
                        className={`voice-glow voice-glow--${phase}`}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.35 }}
                        aria-hidden="true"
                    />
                )}
            </AnimatePresence>

            <AnimatePresence>
                {shown && (
                    <motion.div
                        key="voice-pill"
                        className={`voice-pill voice-pill--${shown.phase}${expanded ? ' voice-pill--expanded' : ''}`}
                        initial={{ opacity: 0, y: -16, scale: 0.96 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: -12, scale: 0.97 }}
                        transition={{ type: 'spring', stiffness: 420, damping: 32 }}
                        onClick={() => setExpanded((v) => !v)}
                        role="status"
                        aria-live="polite"
                    >
                        <div className="voice-pill__row">
                            <span className="voice-pill__icon">
                                <PhaseIcon phase={shown.phase} />
                            </span>
                            <span className="voice-pill__label">
                                {shown.error || PHASE_LABEL[shown.phase]}
                            </span>
                            {shown.transcript && !expanded && (
                                <span className="voice-pill__transcript">
                                    “{shown.transcript}”
                                </span>
                            )}
                        </div>

                        {expanded && (
                            <div className="voice-pill__detail">
                                {shown.transcript && (
                                    <p className="voice-pill__detail-line">
                                        <span className="voice-pill__who">You</span>
                                        {shown.transcript}
                                    </p>
                                )}
                                {shown.reply && (
                                    <p className="voice-pill__detail-line">
                                        <span className="voice-pill__who">Chad</span>
                                        {shown.reply}
                                    </p>
                                )}
                            </div>
                        )}
                    </motion.div>
                )}
            </AnimatePresence>

            <AnimatePresence>
                {showUnavailable && (
                    <motion.div
                        key="voice-unavailable"
                        className="voice-unavailable"
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 8 }}
                        title={availability.remedy ?? undefined}
                    >
                        <div className="voice-unavailable__header">
                            <span className="voice-unavailable__head">
                                <MicOff size={13} />
                                <span className="voice-unavailable__message">
                                    {availability.message ?? 'Voice mode unavailable'}
                                </span>
                            </span>
                            <button
                                type="button"
                                className="voice-unavailable__close"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    setDismissed(true);
                                }}
                                aria-label="Dismiss voice status"
                                title="Dismiss"
                            >
                                <X size={12} />
                            </button>
                        </div>
                        {availability.remedy && (
                            <span
                                className={`voice-unavailable__remedy${onOpenSettings ? ' is-clickable' : ''}`}
                                onClick={() => {
                                    if (onOpenSettings) {
                                        setDismissed(true);
                                        onOpenSettings();
                                    }
                                }}
                            >
                                {availability.remedy}
                            </span>
                        )}
                    </motion.div>
                )}
            </AnimatePresence>
        </>
    );
}
