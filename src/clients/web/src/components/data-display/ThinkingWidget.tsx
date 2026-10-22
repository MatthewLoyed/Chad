import { formatThoughtDurationLabel } from './thoughtDurationLabel';
import { useState, useMemo, memo, useEffect } from 'react';
import type { ThinkingEvent } from '../../store';
import CollapsibleMarkdown from '../common/CollapsibleMarkdown';
import CollapseRail from '../common/CollapseRail';
import ToolMenuCarousel, { type ToolMenuData } from './ToolMenuCarousel';
import { FaChevronDown } from 'react-icons/fa';
import './ThinkingWidget.css';

interface ThinkingWidgetProps {
    events: ThinkingEvent[];
    isActive?: boolean;
    isHistory?: boolean;
    durationMs?: number;
    startTime?: number;
    /** Live pre-first-token phase label ("Selecting tools…", "Loading gemma…", "Thinking…").
     * When set on an active widget it replaces the generic "Thinking…" header text. */
    status?: string;
    streamingRef?: React.RefObject<HTMLSpanElement | null>;
    /** Resolved tool menu from hybrid selection — shuffled chip carousel during wait. */
    toolMenu?: ToolMenuData | null;
    /** Query-tailored narrator line (rotated client-side from backend lines). */
    narratorLine?: string | null;
}

const isMergeable = (type: string) => ['thought', 'thinking', 'reasoning', 'model_loading'].includes(type);

const ELLIPSIS_FRAMES = ['', '.', '..', '…'] as const;

/**
 * Honest completed-thinking header. Never invents "1 seconds" when duration is unknown —
 * that fallback made long turns look like 1s whenever thinkingEndTime wasn't stamped.
 */


/** Animated active-phase header: cycling ellipsis + blinking cursor (Claude-style). */
const ActivePhaseLabel = memo(function ActivePhaseLabel({
    phase,
    seconds,
}: {
    phase: string;
    seconds: number | null;
}) {
    const [frame, setFrame] = useState(0);

    useEffect(() => {
        const id = window.setInterval(() => {
            setFrame((f) => (f + 1) % ELLIPSIS_FRAMES.length);
        }, 420);
        return () => window.clearInterval(id);
    }, []);

    const suffix = ELLIPSIS_FRAMES[frame];
    const base = phase.replace(/\.+$|…+$/u, '').trimEnd();

    return (
        <span className="thinking-label thinking-label--live">
            <span className="thinking-label__shimmer" key={base}>
                {base}
                <span className="thinking-label__ellipsis">{suffix}</span>
            </span>
            <span className="thinking-label__cursor" aria-hidden="true">▍</span>
            {seconds !== null && (
                <span className="thinking-label__timer"> ({seconds}s)</span>
            )}
        </span>
    );
});

const StreamingRawText = memo(({ streamingRef }: { streamingRef?: React.RefObject<HTMLSpanElement | null> }) => {
    return (
        <span 
            ref={streamingRef as React.RefObject<HTMLSpanElement>}
            className="streaming-raw-thinking-text" 
            style={{ whiteSpace: 'pre-wrap', opacity: 0.8, fontSize: '0.95em' }} 
        />
    );
}, () => true); // Never re-render to prevent React from clearing manually appended DOM nodes

export default function ThinkingWidget({
    events,
    isActive = false,
    durationMs,
    startTime,
    status,
    streamingRef,
    toolMenu,
    narratorLine,
}: ThinkingWidgetProps) {
    const [prevActive, setPrevActive] = useState(isActive);
    const [isOpen, setIsOpen] = useState(isActive);
    const [elapsedMs, setElapsedMs] = useState<number>(0);

    if (isActive !== prevActive) {
        setPrevActive(isActive);
        if (isActive) {
            setIsOpen(true);
        }
    }

    // Zero the timer when the turn stops running, alongside the open/close adjustment
    // above rather than inside the interval effect — the effect owns ticking, not reset.
    const running = isActive && !!startTime;
    const [prevRunning, setPrevRunning] = useState(running);
    if (prevRunning !== running) {
        setPrevRunning(running);
        if (!running) setElapsedMs(0);
    }

    useEffect(() => {
        // Re-checked here (rather than using `running`) so TypeScript narrows startTime.
        if (!isActive || !startTime) {
            return;
        }

        const updateTimer = () => {
            const currentElapsed = Date.now() - startTime;
            setElapsedMs(currentElapsed > 0 ? currentElapsed : 0);
        };

        // Initial update
        updateTimer();

        const interval = setInterval(updateTimer, 500);
        return () => clearInterval(interval);
    }, [isActive, startTime]);
    
    // Group consecutive events of the same type (thoughts/thinking/reasoning)
    // Memoized to prevent expensive grouping on every re-render
    const displayEvents = useMemo(() => {
        const merged: ThinkingEvent[] = [];
        
        events.forEach(event => {
            // Include turn_complete events
            if (event.type === 'turn_complete') {
                merged.push({ ...event });
                return;
            }

            // For text-based events, skip if no text
            if (!event.text) return;

            // Parallel research workers execute concurrently; suppress internal scratchpad thoughts
            if (event.agent_name && event.agent_name.toLowerCase().startsWith('research worker')) {
                return;
            }

            const last = merged[merged.length - 1];

            if (last && isMergeable(last.type) && last.type === event.type && last.agent_name === event.agent_name) {
                // Append text to the last mergeable event
                last.text = (last.text || '') + (event.text || '');
                if (event.durationMs !== undefined && event.durationMs > 0) {
                    last.durationMs = event.durationMs;
                }
            } else {
                // Filter only relevant text events
                if (isMergeable(event.type)) {
                    merged.push({ ...event });
                }
            }
        });

        return merged;
    }, [events]);

    const phaseText = useMemo(() => {
        if (!isActive) return null;
        return status || (startTime ? 'Thinking' : 'Processing prompt');
    }, [isActive, status, startTime]);

    const elapsedSeconds = isActive && startTime ? Math.floor(elapsedMs / 1000) : null;

    const completedLabel = useMemo(() => {
        if (displayEvents.length === 0 && toolMenu && toolMenu.tools && toolMenu.tools.length > 0) {
            return 'Toolkit';
        }
        let ms = durationMs;
        if (ms === undefined || ms === 0) {
            const eventWithDuration = events?.find(e => e.durationMs !== undefined && e.durationMs > 0);
            if (eventWithDuration) {
                ms = eventWithDuration.durationMs;
            }
        }
        if ((ms === undefined || ms === 0) && events && events.length > 1) {
            const first = events[0];
            const last = events[events.length - 1];
            if (first.timestamp && last.timestamp && last.timestamp > first.timestamp) {
                ms = last.timestamp - first.timestamp;
            }
        }
        return formatThoughtDurationLabel(ms);
    }, [durationMs, events, displayEvents.length, toolMenu]);

    const lastThoughtPreview = useMemo(() => {
        if (isActive || isOpen) return null;
        const lastTextEvent = [...displayEvents].reverse().find(e => isMergeable(e.type) && e.text);
        if (!lastTextEvent?.text) return null;
        const lastLine = lastTextEvent.text.trim().split('\n').filter(Boolean).pop() || '';
        return lastLine.length > 80 ? lastLine.slice(0, 80) + '…' : lastLine;
    }, [displayEvents, isActive, isOpen]);

    const hasToolMenu = Boolean(toolMenu && toolMenu.tools && toolMenu.tools.length > 0);
    if (displayEvents.length === 0 && !hasToolMenu && !isActive) return null;

    return (
        <div className="thinking-widget">
            <button
                className={`thinking-header ${isOpen ? 'is-open' : ''}`}
                onClick={() => setIsOpen(!isOpen)}
                title={isOpen ? "Collapse thoughts" : "Expand thoughts"}
            >
                <div className="thinking-indicator">
                    <div className="thinking-icon-wrapper">
                        <div className={`thinking-pulse-dot ${isActive ? 'is-active' : ''}`} />
                    </div>
                    <div className="thinking-label-group">
                        {isActive && phaseText ? (
                            <ActivePhaseLabel phase={phaseText} seconds={elapsedSeconds} />
                        ) : (
                            <span className="thinking-label">{completedLabel}</span>
                        )}
                        {lastThoughtPreview && (
                            <span className="thinking-preview">{lastThoughtPreview}</span>
                        )}
                    </div>
                </div>
                <div className={`thinking-chevron ${isOpen ? 'is-open' : ''}`}>
                    <FaChevronDown size={12} />
                </div>
            </button>

            <div
                className={`thinking-content ${isOpen ? 'is-open' : ''}`}
                aria-hidden={!isOpen}
            >
                <div className="thinking-content-clip">
                    <CollapseRail
                        active={isOpen}
                        onCollapse={() => setIsOpen(false)}
                        label="Collapse thoughts"
                        bodyClassName="thinking-content-body"
                    >
                        {toolMenu && toolMenu.tools.length > 0 && (
                            <ToolMenuCarousel menu={toolMenu} isActive={isActive} />
                        )}
                        {isActive && narratorLine && (
                            <p className="wait-narrator-line" key={narratorLine}>{narratorLine}</p>
                        )}
                        {displayEvents.map((event, i) => (
                            <div key={i} className="thinking-item">
                                {/* Thinking text */}
                                {isMergeable(event.type) && event.text && (
                                    <div className="thinking-bubble-wrapper">
                                        {event.agent_name && (
                                            <div className="thinking-agent-tag">
                                                {event.agent_name}
                                            </div>
                                        )}
                                        <CollapsibleMarkdown
                                            content={event.text}
                                            forceExpanded={isActive}
                                        />
                                    </div>
                                )}

                                {/* Turn complete */}
                                {event.type === 'turn_complete' && (
                                    <div className="thinking-item turn-complete-indicator">
                                        ✓ Turn complete
                                    </div>
                                )}
                            </div>
                        ))}
                        {isActive && (
                            <div key="active-thinking-raw-container" className="thinking-item">
                                {/* Direct DOM appending (High performance path - No Live Markdown) */}
                                <StreamingRawText streamingRef={streamingRef} />
                                <span className="thought-text thinking-stream-cursor" aria-hidden="true">▍</span>
                            </div>
                        )}
                    </CollapseRail>
                </div>
            </div>
        </div>
    );
}
