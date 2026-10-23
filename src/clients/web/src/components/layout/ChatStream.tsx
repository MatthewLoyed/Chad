import React, { useRef, useEffect, useState, useMemo, memo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useChatMessages, useAppStore, useStatus, useActiveChatStreamTurn, useActiveChatStatus, useActiveChatPendingToolApproval } from '../../store';
import type { ThinkingEvent } from '../../store';
import MarkdownMessage from '../data-display/MarkdownMessage';
import ThinkingWidget from '../data-display/ThinkingWidget';
import type { ToolMenuData } from '../data-display/ToolMenuCarousel';
import ClarifyingQuestionsCard from '../data-display/ClarifyingQuestionsCard';
import type { ClarifyingQuestion } from '../data-display/ClarifyingQuestionsCard';
import { parseResolvedQaPairs } from '../data-display/clarifying-questions-utils';
import { partsToThinkingEvents } from '../../protocol/streamReducer';
import ArtifactCard from '../data-display/ArtifactCard';
import SourcesFooter from '../data-display/SourcesFooter';
import { extractSourcesFromThoughts, collectTurnMedia, type SourceEntry } from '../../utils/sources';
import YouTubePlayerCard from '../data-display/YouTubePlayerCard';
import { resolveYouTubeDisplay } from '../../utils/youtubeDisplay';
import PlacesMapCard from '../data-display/PlacesMapCard';
import { resolveMapDisplay } from '../../utils/mapDisplay';
import { useWaitTheaterStatus } from '../../hooks/useWaitTheaterStatus';
import { useWaitNarratorLine } from '../../hooks/useWaitNarratorLine';
import { Check, X, Copy, Pencil, FileText, Image as ImageIcon, ThumbsUp, ThumbsDown, Zap, Layers, ChevronDown, Loader2, RotateCw } from 'lucide-react';
import type { ChatMessage } from '../../store';
import './ChatStream.css';

interface ChatStreamProps {
    /** Re-attempt an outbound USER bubble that never reached the Brain. */
    onRetryFailedSend?: (messageId: string, text: string) => void;
    /** Retry or regenerate from a specific prompt (Claude/ChatGPT style). */
    onRetryPrompt?: (messageId: string, text: string) => void;
}

/** Reconstruct a clarifying reply from the preceding AI approval when history lacks clarifyingReply. */
function clarifyingReplyFromPrior(
    prevMsg: ChatMessage | null | undefined,
    msg: ChatMessage,
): { questions: ClarifyingQuestion[]; userChoice: string } | null {
    if (msg.role !== 'user' || msg.clarifyingReply) return null;
    const actions = prevMsg?.actions;
    if (
        prevMsg?.role === 'ai'
        && actions?.type === 'tool_approval'
        && actions.decisionType === 'questions'
        && actions.resolved
        && actions.userChoice
        && actions.questions?.length
    ) {
        // Adjacency alone is not enough. When the user answers inside the card the choice
        // is recorded on the AI message and no separate user message is created, so the
        // NEXT thing they type sits directly after the resolved questions — and used to
        // inherit the card, showing the old questions on an unrelated follow-up. Only
        // treat this message as the reply when its text is actually the recorded answer.
        const answerText = parseResolvedQaPairs(actions.questions, actions.userChoice)
            .map((pair) => pair.answer)
            .join(' · ')
            .trim();
        if (!answerText || msg.text.trim() !== answerText) return null;
        return { questions: actions.questions, userChoice: actions.userChoice };
    }
    return null;
}
/** Stable empty array so renderEvents keeps identity when there's no active turn. */
const EMPTY_EVENTS: ThinkingEvent[] = [];

function mapStopReason(reason?: string): string {
    if (!reason) return 'Unknown';
    switch (reason) {
        case 'task_completed':
            return 'Completed';
        case 'awaiting_approval':
            return 'Awaiting plan approval';
        case 'awaiting_input':
            return 'Awaiting your input';
        case 'human_needed':
            return 'Human Intervention Needed';
        case 'max_iterations':
            return 'Max Iterations Reached';
        case 'length':
            return 'Length Limit Exceeded';
        case 'error':
            return 'Error Encountered';
        default:
            return reason;
    }
}

interface TelemetryRowProps {
    speed?: number;
    tokens?: number;
    stopReason?: string;
}

function TelemetryRow({ speed, tokens, stopReason }: TelemetryRowProps) {
    if (!tokens && !speed && !stopReason) return null;

    return (
        <div className="chat-message__telemetry">
            {speed !== undefined && speed > 0 && (
                <span className="telemetry-pill" title="Completion tokens divided by total turn time, including prompt processing, orchestration, and tools. Not model generation speed.">
                    <Zap size={12} className="telemetry-icon" />
                    {speed.toFixed(2)} tok/s · turn avg
                </span>
            )}
            {tokens !== undefined && tokens > 0 && (
                <span className="telemetry-pill" title="Completion tokens">
                    <Layers size={12} className="telemetry-icon" />
                    {tokens} tokens
                </span>
            )}
            {stopReason && stopReason !== 'task_completed' && (
                <span className="telemetry-pill" title={`Stop reason: ${mapStopReason(stopReason)}`}>
                    Stop reason: {mapStopReason(stopReason)}
                </span>
            )}
        </div>
    );
}

/** Copy/confirm glyph that springs the checkmark in when text is copied. */
function CopyGlyph({ copied }: { copied: boolean }) {
    return (
        <motion.span
            key={copied ? 'check' : 'copy'}
            initial={{ scale: 0.4, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 600, damping: 15 }}
            style={{ display: 'inline-flex' }}
        >
            {copied ? <Check size={14} style={{ color: '#22c55e' }} /> : <Copy size={14} />}
        </motion.span>
    );
}

export default function ChatStream({ onRetryFailedSend, onRetryPrompt }: ChatStreamProps) {
    const messages = useChatMessages();
    const [copiedId, setCopiedId] = useState<string | null>(null);
    const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
    const [editText, setEditText] = useState('');
    // The single render source: the typed reducer turn (streamTurn) adapted into
    // the flat event list AIResponseRenderer consumes. The legacy imperative-DOM /
    // activeTurn render path has been removed — see docs/features/STREAMING_REFACTOR.md.
    const streamTurn = useActiveChatStreamTurn();
    const renderEvents = useMemo(
        () => (streamTurn ? partsToThinkingEvents(streamTurn) : EMPTY_EVENTS),
        [streamTurn],
    );
    // When a live turn is streaming it owns the assistant render, so hide any
    // server-side in-progress ASSISTANT snapshot (merged at read time for cold
    // loads) to avoid a duplicate bubble. The in-progress USER message — the
    // question — always stays. With no live turn (fresh reload mid-turn) the
    // snapshot renders so nothing is lost.
    const displayMessages = useMemo(
        () => (streamTurn ? messages.filter(m => !(m.role === 'ai' && m.in_progress)) : messages),
        [messages, streamTurn],
    );
    // Shared by AIResponseRenderer (for inline [N] citations) and SourcesFooter below.
    const liveSources = useMemo(() => extractSourcesFromThoughts(renderEvents), [renderEvents]);
    const resolveMessageAction = useAppStore(state => state.resolveMessageAction);
    const activeConversationId = useAppStore(state => state.activeConversationId);
    const submitFeedback = useAppStore(state => state.submitFeedback);
    const status = useStatus();
    const activeChatStatus = useActiveChatStatus();
    const isBusy = status === 'thinking' || streamTurn?.lifecycle === 'running';
    const apiReady = useAppStore(state => state.apiReady);
    const tokenUsage = useAppStore(state => state.tokenUsage);
    const turnStartTime = useAppStore(state => state.turnStartTime);
    const liveStatus = useAppStore(state => state.liveStatus);
    const liveStatusUpdatedAt = useAppStore(state => state.liveStatusUpdatedAt);
    const liveNarratorLines = useAppStore(state => state.liveNarratorLines);
    const pendingToolApproval = useActiveChatPendingToolApproval();
    const prepareToolApprovalFollowUp = useAppStore(state => state.prepareToolApprovalFollowUp);
    const [showScrollPill, setShowScrollPill] = useState(false);

    // Live-turn booleans derived purely from the typed render events — no imperative
    // hasStartedStreaming/thinkingStarted state (the old DOM-append path set those).
    const hasReasoningStarted = useMemo(
        () => renderEvents.some(e => e.type === 'reasoning' || e.type === 'thought' || e.type === 'thinking'),
        [renderEvents],
    );
    // Anything past pure prelude (tools, plans, answer) — drives the streaming shell /
    // telemetry. Tools intentionally count here so tool cards mount in the live bubble.
    const hasContentStarted = useMemo(
        () => renderEvents.some(
            e => !['turn_start', 'model_loading', 'reasoning', 'thought', 'thinking', 'tool_menu'].includes(e.type),
        ),
        [renderEvents],
    );
    // Answer text only. Thinking phase (live timer / Thought-for label) must stay open
    // through tools — ending it on tool_start was the "Thought for 1 seconds" lie while
    // tools kept running for much longer.
    const hasAnswerStarted = useMemo(
        () => renderEvents.some(e => e.type === 'speech'),
        [renderEvents],
    );

    const thinkingStartTime = useAppStore(state => state.thinkingStartTime);
    const thinkingEndTime = useAppStore(state => state.thinkingEndTime);
    const setThinkingStartTime = useAppStore(state => state.setThinkingStartTime);
    const setThinkingEndTime = useAppStore(state => state.setThinkingEndTime);

    useEffect(() => {
        if (hasReasoningStarted && !thinkingStartTime) {
            const firstReasoningEvent = renderEvents.find(
                e => e.type === 'reasoning' || e.type === 'thought' || e.type === 'thinking'
            );
            setThinkingStartTime(firstReasoningEvent?.timestamp || Date.now());
        }
    }, [hasReasoningStarted, thinkingStartTime, renderEvents, setThinkingStartTime]);

    useEffect(() => {
        if (hasAnswerStarted && thinkingStartTime && !thinkingEndTime) {
            const firstSpeech = renderEvents.find(e => e.type === 'speech');
            setThinkingEndTime(firstSpeech?.timestamp || Date.now());
        }
    }, [hasAnswerStarted, thinkingStartTime, thinkingEndTime, renderEvents, setThinkingEndTime]);

    const [liveSpeed, setLiveSpeed] = useState(0);
    useEffect(() => {
        if (!turnStartTime || (!hasContentStarted && !hasReasoningStarted)) {
            Promise.resolve().then(() => setLiveSpeed(0));
            return;
        }
        const elapsed = (Date.now() - turnStartTime) / 1000;
        const compTokens = tokenUsage?.completion_tokens || 0;
        Promise.resolve().then(() => setLiveSpeed(elapsed > 0 ? compTokens / elapsed : 0));
    }, [turnStartTime, hasContentStarted, hasReasoningStarted, tokenUsage?.completion_tokens]);


    const scrollRef = useRef<HTMLDivElement>(null);
    const bottomRef = useRef<HTMLDivElement>(null);
    const isAtBottomRef = useRef(true);
    const isProgrammaticScrollRef = useRef(false);
    const lastMessageCountRef = useRef(messages.length);

    // Track if we should force scroll (e.g. user just sent a message)
    useEffect(() => {
        if (messages.length > lastMessageCountRef.current) {
            const lastMsg = messages[messages.length - 1];
            if (lastMsg.role === 'user') {
                // User sent a message, force scroll to bottom
                isAtBottomRef.current = true;
                isProgrammaticScrollRef.current = true;
                bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
            }
        }
        lastMessageCountRef.current = messages.length;
    }, [messages]);

    const handleScroll = () => {
        if (!scrollRef.current) return;
        if (isProgrammaticScrollRef.current) {
            isProgrammaticScrollRef.current = false;
            return;
        }
        const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
        // 80px buffer allows natural sub-pixel breathing room
        const atBottom = scrollHeight - scrollTop <= clientHeight + 80;
        isAtBottomRef.current = atBottom;
        setShowScrollPill(!atBottom);
    };

    const scrollToBottom = (smooth = true) => {
        isAtBottomRef.current = true;
        setShowScrollPill(false);
        if (scrollRef.current) {
            isProgrammaticScrollRef.current = true;
            if (smooth) {
                bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
            } else {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            }
        }
    };

    // (The legacy imperative-DOM append path — window.appendStreamToken /
    // appendThinkingToken, the raw-text refs, the global ref exposure, and the
    // status-driven DOM cleanup effects — has been removed. Thinking and answer
    // text now render from the typed reducer's parts via AIResponseRenderer.)

    // Show active widget if we have thoughts OR if the system is in a thinking state.
    // streamTurn + liveStatus cover the pre-first-token gap (tool selection / model load)
    // before the backend flips status to 'thinking' or reasoning events arrive.
    //
    // AppStatus can stick at model_loading (optimistic local send, or a missed
    // thinking flip) while the turn has already moved on — toolkit / tools /
    // reasoning. Don't keep the Loading… banner once any of those are present;
    // LM Studio residency is not what clears this UI.
    const turnPastModelLoad = hasReasoningStarted
        || renderEvents.some(e => e.type === 'tool_menu' || e.type === 'tool_start' || e.type === 'tool_end');
    const isModelLoading = activeChatStatus === 'model_loading' && !turnPastModelLoad;
    const isThinking = activeChatStatus === 'thinking' || activeChatStatus === 'executing' || activeChatStatus === 'active';
    const isTurnInFlight = streamTurn?.lifecycle === 'running';
    // Pre-answer wait includes tools — only speech closes the live thinking phase.
    const isPreFirstToken = isTurnInFlight && !hasAnswerStarted;
    const liveStateScopedToActiveChat = streamTurn != null || activeChatStatus !== 'ready';
    const activeChatLiveStatus = liveStateScopedToActiveChat ? liveStatus : null;
    const activeChatLiveNarratorLines = liveStateScopedToActiveChat ? liveNarratorLines : null;
    const showActiveThinking = renderEvents.length > 0 || isThinking || hasReasoningStarted || !!activeChatLiveStatus || isPreFirstToken
        || renderEvents.some(e => e.type === 'tool_menu');
    const showStreamingContent = hasContentStarted;
    const isActiveTurn = (isThinking || hasReasoningStarted || !!activeChatLiveStatus || isPreFirstToken) && !hasAnswerStarted;
    // Plan may land after thinking stops; keep approval affordances until the turn commits or content starts.
    const liveAwaitingPlanApproval = useMemo(
        () => renderEvents.some((e) => e.type === 'execution_plan') && !hasAnswerStarted,
        [renderEvents, hasAnswerStarted],
    );
    const displayLiveStatus = useWaitTheaterStatus(
        activeChatLiveStatus,
        liveStatusUpdatedAt,
        isActiveTurn,
    );
    const displayNarratorLine = useWaitNarratorLine(
        activeChatLiveNarratorLines,
        isActiveTurn,
    );
    
    // DEBUG: Log active turn visibility state
    // console.log(`[DEBUG ChatStream] status=${status} currentThoughts=${currentThoughts.length} thinkingStarted=${thinkingStarted} showActiveThinking=${showActiveThinking} showStreaming=${showStreamingContent}`);

    const activeModel = useAppStore(state => state.activeModel);
    const modelLoadingEvent = renderEvents.find(e => e.type === 'model_loading');
    
    // Preference: 1. Real event from backend, 2. Active model info, 3. Generic fallback
    const loadingName = modelLoadingEvent?.name || (activeModel?.mode === 'local' ? activeModel?.name : '');
    const loadingProvider = modelLoadingEvent?.provider || (activeModel?.mode === 'local' ? activeModel?.provider : '');
    
    const loadingMessage = loadingName 
        ? `Loading ${loadingName}${loadingProvider ? ` from ${loadingProvider}` : ''}...`
        : "Loading local model...";

    // Continuous lock-to-bottom observer for dynamically expanding widgets (thinking accordion, tool output, streaming text)
    useEffect(() => {
        const el = scrollRef.current;
        if (!el || typeof MutationObserver === 'undefined') return;

        let frameId: number | null = null;
        const observer = new MutationObserver(() => {
            if (isAtBottomRef.current && el) {
                if (frameId) cancelAnimationFrame(frameId);
                frameId = requestAnimationFrame(() => {
                    if (isAtBottomRef.current && el) {
                        isProgrammaticScrollRef.current = true;
                        el.scrollTop = el.scrollHeight;
                    }
                });
            }
        });

        observer.observe(el, { childList: true, subtree: true, characterData: true });

        return () => {
            observer.disconnect();
            if (frameId) cancelAnimationFrame(frameId);
        };
    }, []);

    // Autoscroll to bottom when messages change or thoughts/streaming update
    useEffect(() => {
        if (!isAtBottomRef.current || !scrollRef.current) return;
        isProgrammaticScrollRef.current = true;
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }, [messages.length, renderEvents.length, showActiveThinking, hasContentStarted, streamTurn]);

    // Handle approval button clicks
    const handleApproval = async (messageId: string, token: string, approved: boolean) => {
        navigator.vibrate?.(10);
        // Update UI immediately
        resolveMessageAction(messageId, approved ? 'approved' : 'rejected');

        // Call Python API
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.confirm_file_plan_approval === 'function') {
                await api.confirm_file_plan_approval(token, approved);
            }
        } catch (e) {
            console.error('[ChatStream] Error sending approval:', e);
        }
    };

    const handlePlanApproval = async (approved: boolean) => {
        const api = window.pywebview?.api;
        if (api && typeof api.send_text_command === 'function') {
            const state = useAppStore.getState();
            const text = approved ? 'go ahead' : 'cancel plan';
            const outboundId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
            state.addChatMessage('user', text, undefined, undefined, outboundId, { sendStatus: 'sending' });
            // Plan finalization releases stream ownership. The desktop bridge
            // only emits events; reclaim the chat before any continuation arrives.
            state.setStreamConversationId(activeConversationId);
            state.setStatus('thinking');
            state.setStatusText('Processing Prompt...');
            state.beginTurnWait();
            try {
                const sent = await api.send_text_command(text, null, null, activeConversationId, null, null);
                if (sent === false) state.failOutboundSend(outboundId);
                else state.setMessageSendStatus(outboundId, null);
            } catch (e) {
                console.error('[ChatStream] Error sending plan approval:', e);
                state.failOutboundSend(outboundId);
            }
        }
    };

    // Handle generic tool-approval card resolution (accept/decline or MCQ choice)
    const handleToolApprovalResolve = (_messageId: string, token: string, decision: string, displayText?: string) => {
        navigator.vibrate?.(10);
        prepareToolApprovalFollowUp(decision, displayText);
        const api = window.pywebview?.api;
        if (api && typeof api.resolve_tool_approval === 'function') {
            api.resolve_tool_approval(token, decision, displayText);
        }
    };

    const handlePendingToolApprovalResolve = (decision: string, displayText?: string) => {
        if (!pendingToolApproval) return;
        navigator.vibrate?.(10);
        prepareToolApprovalFollowUp(decision, displayText);
        const api = window.pywebview?.api;
        if (api && typeof api.resolve_tool_approval === 'function') {
            api.resolve_tool_approval(pendingToolApproval.token, decision, displayText);
        }
    };

    // Handle copy to clipboard
    const handleCopy = (id: string, text: string) => {
        navigator.clipboard.writeText(text);
        setCopiedId(id);
        setTimeout(() => setCopiedId(null), 2000);
    };

    const handleFeedback = (messageId: string, rating: 'positive' | 'negative') => {
        if (!activeConversationId || messageId.startsWith('msg-')) return;
        submitFeedback(activeConversationId, messageId, rating);
    };

    // Helper to extract successful tool executions with download tokens
    const getArtifacts = (thoughts?: ThinkingEvent[]) => {
        if (!thoughts) return [];
        return thoughts.filter(e => e.type === 'tool_end' && e.success && e.download_token);
    };

    return (
        <div
            ref={scrollRef}
            className="chat-stream"
            onScroll={handleScroll}
        >
            <AnimatePresence initial={false}>
                {displayMessages.map((msg, idx) => {
                    const prevMsg = idx > 0 ? displayMessages[idx - 1] : null;
                    const isContinued = prevMsg?.role === msg.role;
                    
                    // Show separator only if more than 1 hour has passed since the previous message
                    const hasTimeGap = prevMsg && (new Date(msg.timestamp).getTime() - new Date(prevMsg.timestamp).getTime() > 60 * 60 * 1000);

                    return (
                    <React.Fragment key={msg.id}>
                        {/* Turn separator between AI response and next user message */}
                        {hasTimeGap && prevMsg && (
                            <div className="turn-separator">
                                <div className="turn-separator__line" />
                                <span className="turn-separator__time">
                                    {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                </span>
                                <div className="turn-separator__line" />
                            </div>
                        )}

                    <motion.div
                        className={`chat-message-item chat-message-item--${msg.role}${isContinued ? ' chat-message-item--continued' : ''}`}
                        initial={{ opacity: 0, y: 20, scale: 0.95 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.95 }}
                        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                    >
                        <div className={`chat-message chat-message--${msg.role}${msg.clarifyingReply || clarifyingReplyFromPrior(prevMsg, msg) ? ' chat-message--clarifying-reply' : ''}`}>
                            {msg.role === 'ai' ? (
                                <>
                                    {msg.actions?.type === 'file_plan_approval' ? (() => {
                                        try {
                                            const plan = JSON.parse(msg.text);
                                            // Index against the SAME list we're mapping. `messages` is
                                            // unfiltered, so mid-turn (when displayMessages drops the
                                            // in-progress AI message) the last plan card was always
                                            // "before the end" and collapsed as though historical.
                                            return <PlanCard plan={plan} isActive={!msg.actions!.resolved} isHistory={idx < displayMessages.length - 1} />;
                                        } catch {
                                            return <MarkdownMessage content={msg.text} />;
                                        }
                                    })() : (
                                        <>
                                            <AIResponseRenderer
                                                events={msg.thoughts}
                                                text={
                                                    msg.actions?.type === 'tool_approval' && msg.thoughts?.length
                                                        ? undefined
                                                        : msg.text
                                                }
                                                isHistory={!msg.in_progress}
                                                awaitingPlanApproval={msg.stop_reason === 'awaiting_approval'}
                                                onPlanApproval={handlePlanApproval}
                                                historyDurationMs={msg.thinkingDurationMs ?? msg.turnDurationMs}
                                                sources={msg.sources}
                                            />
                                            {msg.actions?.type === 'tool_approval' && (
                                                <ToolApprovalPanel
                                                    approval={msg.actions}
                                                    thoughts={msg.thoughts}
                                                    onResolve={(decision, displayText) =>
                                                        handleToolApprovalResolve(
                                                            msg.id,
                                                            msg.actions?.type === 'tool_approval' ? msg.actions.token : '',
                                                            decision,
                                                            displayText,
                                                        )
                                                    }
                                                />
                                            )}
                                        </>
                                    )}

                                    {/* Render Inline Artifact Cards for History */}
                                    {(msg.artifacts || getArtifacts(msg.thoughts)).map((artifact, i) => (
                                        <ArtifactCard key={`artifact-${i}`} artifact={artifact} />
                                    ))}

                                    {/* Expandable Sources footer for research turns (issue #333) */}
                                    <SourcesFooter sources={msg.sources || []} />
                                </>
                            ) : (
                                (() => {
                                    const reply = msg.clarifyingReply || clarifyingReplyFromPrior(prevMsg, msg);
                                    if (reply) {
                                        return (
                                            <ClarifyingQuestionsCard
                                                questions={reply.questions}
                                                resolved
                                                userChoice={reply.userChoice}
                                            />
                                        );
                                    }
                                    if (editingMessageId === msg.id) {
                                        return (
                                            <div className="chat-message__edit-container">
                                                <textarea
                                                    className="chat-message__edit-textarea"
                                                    value={editText}
                                                    onChange={(e) => setEditText(e.target.value)}
                                                    rows={Math.min(10, Math.max(2, editText.split('\n').length))}
                                                    autoFocus
                                                    aria-label="Edit prompt"
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter' && !e.shiftKey) {
                                                            e.preventDefault();
                                                            if (editText.trim() && !isBusy) {
                                                                const newText = editText.trim();
                                                                setEditingMessageId(null);
                                                                onRetryPrompt?.(msg.id, newText);
                                                            }
                                                        } else if (e.key === 'Escape') {
                                                            setEditingMessageId(null);
                                                        }
                                                    }}
                                                />
                                                <div className="chat-message__edit-actions">
                                                    <button
                                                        type="button"
                                                        className="chat-message__edit-btn chat-message__edit-btn--cancel"
                                                        onClick={() => setEditingMessageId(null)}
                                                    >
                                                        Cancel
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="chat-message__edit-btn chat-message__edit-btn--save"
                                                        disabled={!editText.trim() || isBusy}
                                                        onClick={() => {
                                                            const newText = editText.trim();
                                                            setEditingMessageId(null);
                                                            onRetryPrompt?.(msg.id, newText);
                                                        }}
                                                    >
                                                        Save &amp; Submit
                                                    </button>
                                                </div>
                                            </div>
                                        );
                                    }
                                    return <MarkdownMessage content={msg.text} />;
                                })()
                            )}
                        </div>

                        {msg.role === 'ai' && (
                            <TelemetryRow 
                                speed={msg.speed}
                                tokens={msg.completion_tokens || msg.total_tokens}
                                stopReason={msg.stop_reason}
                            />
                        )}

                        {/* Render Attachments */}
                        {msg.attachments && msg.attachments.length > 0 && (
                            <div className={`chat-message__attachments chat-message__attachments--${msg.role}`}>
                                {msg.attachments.map(file => (
                                    <div key={file.id} className="chat-message__attachment-chip">
                                        {file.type === 'image' ? <ImageIcon size={14} /> : <FileText size={14} />}
                                        <span className="attachment-name">{file.name}</span>
                                        {file.size && <span className="attachment-size">({(file.size / 1024).toFixed(0)}KB)</span>}
                                    </div>
                                ))}
                            </div>
                        )}

                        {msg.role === 'user' && editingMessageId !== msg.id && (
                            <div className="chat-message__user-actions">
                                <button
                                    className="user-action-btn"
                                    onClick={() => handleCopy(msg.id, msg.text)}
                                    title="Copy message"
                                    aria-label="Copy message"
                                >
                                    <CopyGlyph copied={copiedId === msg.id} />
                                </button>
                                <button
                                    className="user-action-btn"
                                    onClick={() => {
                                        setEditingMessageId(msg.id);
                                        setEditText(msg.text);
                                    }}
                                    title="Edit message"
                                    aria-label="Edit message"
                                    disabled={isBusy || msg.sendStatus === 'sending'}
                                >
                                    <Pencil size={14} />
                                </button>
                                {onRetryPrompt && (
                                    <button
                                        type="button"
                                        className="user-action-btn user-action-btn--retry"
                                        onClick={() => onRetryPrompt(msg.id, msg.text)}
                                        title="Retry prompt"
                                        aria-label="Retry prompt"
                                        disabled={isBusy || msg.sendStatus === 'sending'}
                                    >
                                        <RotateCw size={14} />
                                    </button>
                                )}
                            </div>
                        )}

                        {msg.role === 'user' && msg.sendStatus === 'sending' && (
                            <div className="chat-message__pending-status">
                                <Loader2 size={12} className="chat-message__pending-spinner" />
                                <span>
                                    {!apiReady || status === 'initializing'
                                        ? 'Waiting for server to connect...'
                                        : 'Sending…'}
                                </span>
                            </div>
                        )}

                        {msg.role === 'user' && msg.sendStatus === 'failed' && (
                            <div className="chat-message__send-failed">
                                <span>Couldn&apos;t send</span>
                                {onRetryFailedSend && (
                                    <button
                                        type="button"
                                        className="chat-message__retry-btn"
                                        onClick={() => onRetryFailedSend(msg.id, msg.text)}
                                    >
                                        <RotateCw size={12} />
                                        Retry
                                    </button>
                                )}
                            </div>
                        )}

                        {msg.role === 'ai' && (
                            <div className="chat-message__ai-actions">
                                <button
                                    className={`feedback-btn feedback-btn--up ${msg.userFeedback === 'positive' ? 'feedback-btn--active' : ''}`}
                                    onClick={() => handleFeedback(msg.id, 'positive')}
                                    disabled={msg.id.startsWith('msg-')}
                                    title={msg.id.startsWith('msg-') ? "Feedback disabled for transient messages" : "Thumbs up"}
                                >
                                    <ThumbsUp size={14} />
                                </button>
                                <button
                                    className={`feedback-btn feedback-btn--down ${msg.userFeedback === 'negative' ? 'feedback-btn--active' : ''}`}
                                    onClick={() => handleFeedback(msg.id, 'negative')}
                                    disabled={msg.id.startsWith('msg-')}
                                    title={msg.id.startsWith('msg-') ? "Feedback disabled for transient messages" : "Thumbs down"}
                                >
                                    <ThumbsDown size={14} />
                                </button>
                                <button
                                    className="feedback-btn feedback-btn--copy"
                                    onClick={() => handleCopy(msg.id, msg.text)}
                                    title="Copy response"
                                    aria-label="Copy response"
                                >
                                    <CopyGlyph copied={copiedId === msg.id} />
                                </button>
                                {onRetryPrompt && (
                                    <button
                                        type="button"
                                        className="feedback-btn feedback-btn--retry"
                                        onClick={() => {
                                            const prevUserMsg = [...displayMessages.slice(0, idx)].reverse().find(m => m.role === 'user');
                                            if (prevUserMsg) {
                                                onRetryPrompt(prevUserMsg.id, prevUserMsg.text);
                                            }
                                        }}
                                        disabled={isBusy}
                                        title="Regenerate response"
                                        aria-label="Regenerate response"
                                    >
                                        <RotateCw size={14} />
                                    </button>
                                )}
                            </div>
                        )}

                        {/* Render action buttons for file plan approvals */}
                        {msg.actions && msg.actions.type === 'file_plan_approval' && (() => {
                            const filePlanActions = msg.actions;
                            return (
                            <div className="chat-message-actions-overlay">
                                {!filePlanActions.resolved ? (
                                    <div className="chat-message-actions-buttons">
                                        <button
                                            className="action-btn action-btn--approve"
                                            onClick={() => handleApproval(msg.id, filePlanActions.approvalToken, true)}
                                        >
                                            <Check size={16} />
                                            Approve
                                        </button>
                                        <button
                                            className="action-btn action-btn--reject"
                                            onClick={() => handleApproval(msg.id, filePlanActions.approvalToken, false)}
                                        >
                                            <X size={16} />
                                            Reject
                                        </button>
                                    </div>
                                ) : (
                                    <div className={`action-result action-result--${filePlanActions.userChoice}`}>
                                        {filePlanActions.userChoice === 'approved' ? (
                                            <><Check size={16} /> Plan Approved</>
                                        ) : (
                                            <><X size={16} /> Plan Rejected</>
                                        )}
                                    </div>
                                )}
                            </div>
                            );
                        })()}
                    </motion.div>
                    </React.Fragment>
                    );
                })}

                {/* Render active thinking process (pending response), typing indicator, OR streaming content */}
                {(showActiveThinking || showStreamingContent || isModelLoading) && (
                    <motion.div
                        key="active-turn-wrapper"
                        className={`chat-message-item chat-message-item--ai ${renderEvents.length === 0 && !showStreamingContent && !isModelLoading ? 'chat-message-item--typing' : ''}`}
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.9 }}
                        transition={{ duration: 0.2 }}
                    >
                        {/* Unified AI Message Bubble for active turn */}
                        <div className="chat-message chat-message--ai">
                            {isModelLoading && (
                                <motion.div 
                                    className="model-loading-indicator"
                                    initial={{ opacity: 0, y: 5 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0 }}
                                >
                                    <div className="loading-pulse-ring" />
                                    <span className="loading-text">{loadingMessage}</span>
                                </motion.div>
                            )}

                            {/* Render the active turn entirely from the typed reducer parts
                                (thinking, tools, plan, and the streamed answer text). */}
                            <AIResponseRenderer
                                events={renderEvents}
                                isActive={isActiveTurn}
                                awaitingPlanApproval={liveAwaitingPlanApproval}
                                onPlanApproval={handlePlanApproval}
                                thinkingStartTime={thinkingStartTime}
                                thinkingEndTime={thinkingEndTime}
                                liveStatus={isActiveTurn ? displayLiveStatus : null}
                                narratorLine={isActiveTurn ? displayNarratorLine : null}
                                liveStartTime={turnStartTime}
                                sources={liveSources}
                            />

                            {pendingToolApproval && (
                                <ToolApprovalPanel
                                    approval={pendingToolApproval}
                                    thoughts={renderEvents}
                                    onResolve={handlePendingToolApprovalResolve}
                                />
                            )}

                            {/* Inline artifact cards for the active response. */}
                            {getArtifacts(renderEvents).length > 0 && (
                                <div style={{ marginTop: 8 }}>
                                    {getArtifacts(renderEvents).map((artifact, i) => (
                                        <ArtifactCard key={`artifact-active-${i}`} artifact={artifact} />
                                    ))}
                                </div>
                            )}

                            {/* Expandable Sources footer only if rich media exists or before speech starts */}
                            {Boolean(collectTurnMedia(liveSources) || !hasContentStarted) && (
                                <SourcesFooter sources={liveSources} />
                            )}
                            {/* Typing indicator (fallback when nothing else is showing) */}
                            {!isModelLoading && !showActiveThinking && !showStreamingContent && (
                                <div className="typing-indicator" style={{ padding: '4px 0' }}>
                                    <span></span>
                                    <span></span>
                                    <span></span>
                                </div>
                            )}
                        </div>

                        {/* Live Telemetry Row */}
                        {(showStreamingContent || showActiveThinking) && (
                            <TelemetryRow 
                                speed={liveSpeed}
                                tokens={tokenUsage?.completion_tokens}
                            />
                        )}
                    </motion.div>
                )}
            </AnimatePresence>

            {showScrollPill && (
                <div className="scroll-pill-anchor">
                    <button className="scroll-to-bottom-pill" onClick={() => scrollToBottom(true)}>
                        <ChevronDown size={13} />
                        New message
                    </button>
                </div>
            )}

            <div ref={bottomRef} style={{ height: 1, flexShrink: 0 }} />
        </div>
    );
}

/**
 * Memoized renderer for AI message content (interleaved thoughts/speech/tools)
 */
import ToolStackBubble from '../data-display/ToolStackBubble';
import type { StackedToolItem } from '../data-display/ToolStackBubble';
import PlanCard from '../data-display/PlanCard';
import ToolApprovalPanel from '../data-display/ToolApprovalPanel';
import SubagentPanel from '../data-display/SubagentPanel';
import { extractScoutReport } from '../../utils/scout-report';
import { resolveToolUI } from '../../utils/tool-ui';
import { normalizeTurnEvents } from '../../reducers/turnEventReducer';

/** Friendly label for a subagent group node (agent_name attribution > delegate_to_* tool name). */
function friendlySubagentName(node: ThinkingEvent): string {
    if (node.agent_name) return node.agent_name;
    const name = node.name || '';
    if (name === 'scout_gather' || name === 'dispatch_scout') return 'Scout';
    if (name.startsWith('delegate_to_')) {
        return name
            .replace('delegate_to_', '')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, (c) => c.toUpperCase());
    }
    return 'Subagent';
}

/** Extract the prompt/task passed to a subagent from its node or parameters. */
function extractSubagentPrompt(node: ThinkingEvent): string | undefined {
    if (typeof node.prompt === 'string' && node.prompt.trim()) {
        return node.prompt.trim();
    }
    const params = node.params;
    if (params && typeof params === 'object') {
        const candidate =
            params.task ??
            params.prompt ??
            params.instructions ??
            params.instruction ??
            params.brief ??
            params.goal ??
            params.query ??
            params.description;
        if (typeof candidate === 'string' && candidate.trim()) {
            return candidate.trim();
        }
    }
    if (node.children && Array.isArray(node.children)) {
        for (const child of node.children) {
            if (typeof child.prompt === 'string' && child.prompt.trim()) {
                return child.prompt.trim();
            }
            if (child.params && typeof child.params === 'object') {
                const candidate = child.params.task ?? child.params.prompt ?? child.params.instructions;
                if (typeof candidate === 'string' && candidate.trim()) {
                    return candidate.trim();
                }
            }
        }
    }
    return undefined;
}

const AIResponseRenderer = memo(({
    events,
    text,
    isHistory = false,
    isActive = false,
    awaitingPlanApproval = false,
    streamingRef,
    onPlanApproval,
    thinkingStartTime,
    thinkingEndTime,
    liveStatus,
    narratorLine,
    liveStartTime,
    historyDurationMs,
    sources
}: {
    events?: ThinkingEvent[],
    text?: string,
    isHistory?: boolean,
    isActive?: boolean,
    /** Show Proceed on a pending plan after the turn pauses for approval. */
    awaitingPlanApproval?: boolean,
    streamingRef?: React.RefObject<HTMLSpanElement | null>,
    onPlanApproval?: (approved: boolean) => void,
    thinkingStartTime?: number | null,
    thinkingEndTime?: number | null,
    // Pre-first-token phase label + the turn-start time, so the active indicator shows
    // "Selecting tools… (3s)" / "Loading gemma… (12s)" with a running timer during the gap.
    liveStatus?: string | null,
    narratorLine?: string | null,
    liveStartTime?: number | null,
    // Committed messages' reasoning/text parts carry no per-event timestamps (the typed
    // reducer doesn't stamp them), so history can't recompute "Thought for Xs" from events —
    // it always fell back to a hardcoded 1s. This is the duration captured live (from
    // thinkingStartTime/thinkingEndTime) and persisted on the message at finalize time.
    historyDurationMs?: number,
    // Turn's sources (from msg.sources / extractSourcesFromThoughts), threaded into the
    // speech-text MarkdownMessage blocks so inline [N] citation markers can resolve.
    sources?: SourceEntry[]
}) => {
    const toolMenu = useMemo((): ToolMenuData | null => {
        const ev = events?.find(e => e.type === 'tool_menu');
        if (!ev?.tools?.length) return null;
        return {
            tools: ev.tools,
            shortlistSize: ev.shortlist_size,
            selectionPath: ev.selection_path,
        };
    }, [events]);

    const blocks = useMemo(() => {
        const normalizedEvents = events && events.length > 0 ? normalizeTurnEvents(events) : events;
        const treeEvents = normalizedEvents?.filter(e => e.type !== 'tool_menu') ?? normalizedEvents;

        if ((!treeEvents || treeEvents.length === 0) && !toolMenu && !isActive) {
            return [<MarkdownMessage key="base-text" content={text || ''} sources={sources} />];
        }

        // 1. Build hierarchical tree from flat stream.
        //    Subagent attribution: `parent_id` is set ONLY during delegation, so any event
        //    carrying one belongs to its subagent group (keyed by parent_id). A delegation
        //    `tool_start` (category 'delegation') is the launcher/header for the group keyed
        //    by its own id — it labels the panel instead of rendering as a standalone tool.
        //    This groups subagent thoughts + tools into one panel even when the delegation
        //    runs as a graph node and never emits a delegate_to_* tool of its own.
        const buildTree = (flat: ThinkingEvent[]) => {
            const roots: ThinkingEvent[] = [];
            const toolMap = new Map<string, ThinkingEvent>();   // correlation_id -> tool node (tool_end merge)
            const groupMap = new Map<string, ThinkingEvent>();  // parent_id -> subagent group node

            const ensureGroup = (groupId: string, placementParentId?: string): ThinkingEvent => {
                let group = groupMap.get(groupId);
                if (!group) {
                    group = { type: 'subagent_group', correlation_id: groupId, children: [] };
                    groupMap.set(groupId, group);
                    // Nested delegation nests inside its parent group; otherwise it's a root.
                    const placement = placementParentId ? groupMap.get(placementParentId) : undefined;
                    if (placement && placement.children) {
                        placement.children.push(group);
                    } else {
                        roots.push(group);
                    }
                }
                return group;
            };

            flat.forEach(evt => {
                if (evt.type === 'tool_end') {
                    const target = toolMap.get(evt.correlation_id || '');
                    if (target) {
                        Object.assign(target, {
                            success: evt.success,
                            result: evt.result,
                            download_token: evt.download_token,
                            filename: evt.filename,
                            youtube_video_id: evt.youtube_video_id,
                            youtube_title: evt.youtube_title,
                            map_view: evt.map_view,
                            awaiting_user: evt.awaiting_user || target.awaiting_user,
                        });
                    }
                    // A delegation header's own end resolves the subagent panel's status.
                    const headedGroup = groupMap.get(evt.correlation_id || '');
                    if (headedGroup) {
                        Object.assign(headedGroup, { success: evt.success, result: evt.result });
                    }
                    return;
                }

                if (evt.type === 'tool_start' && evt.correlation_id && resolveToolUI(evt).category === 'delegation') {
                    const group = ensureGroup(evt.correlation_id, evt.parent_id);
                    group.name = evt.name;
                    group.display_text = evt.display_text;
                    group.params = evt.params;
                    const extracted = evt.prompt || extractSubagentPrompt(evt);
                    if (extracted) group.prompt = extracted;
                    // Live path inlines tool_end onto tool_start (partsToThinkingEvents); copy
                    // success so the panel can leave "Working…" without a separate tool_end event.
                    if (evt.success !== undefined) {
                        group.success = evt.success;
                        group.result = evt.result;
                    }
                    return;
                }

                const node: ThinkingEvent = { ...evt, children: evt.type === 'tool_start' ? [] : undefined };
                if (evt.correlation_id && evt.type === 'tool_start') {
                    toolMap.set(evt.correlation_id, node);
                }

                // Public browser status must stay visible while the delegation's
                // detailed trace is collapsed. Keep parent_id on the event for
                // attribution, but render this compact status at the chat root.
                if (evt.type === 'browser_progress') {
                    roots.push(node);
                    return;
                }
                if (evt.parent_id) {
                    const group = ensureGroup(evt.parent_id);
                    if (!group.agent_name && evt.agent_name) group.agent_name = evt.agent_name;
                    group.children!.push(node);
                } else {
                    roots.push(node);
                }
            });
            return roots;
        };

        // 2. Recursive rendering function
        const renderNodes = (
            nodes: ThinkingEvent[], 
            options: { isHistory: boolean; isActive: boolean; streamingRef?: React.RefObject<HTMLSpanElement | null>; isRoot?: boolean }
        ): React.ReactNode[] => {
            const isRoot = options.isRoot ?? true;
            const result: React.ReactNode[] = [];
            let currentThinkingSlice: ThinkingEvent[] = [];
            let currentThinkingStartIdx: number | null = null;
            let currentToolStackTracker: { category: string; blockIdx: number } | null = null;
            let hasAttachedToolMenu = false;

            // Count distinct thinking blocks in this nodes array to avoid lumping turn-level duration across multiple blocks
            let totalThinkingBlocks = 0;
            let inBlock = false;
            for (const n of nodes) {
                if (n.type === 'thought' || n.type === 'thinking' || n.type === 'reasoning' || n.type === 'model_loading') {
                    if (!inBlock) {
                        totalThinkingBlocks++;
                        inBlock = true;
                    }
                } else {
                    inBlock = false;
                }
            }

            const flushThinking = (forceActive = false, nextNode?: ThinkingEvent) => {
                if (currentThinkingSlice.length > 0) {
                    const startIdx = currentThinkingStartIdx !== null ? currentThinkingStartIdx : nodes.length;
                    const key = `thinking-block-${startIdx}`;
                    
                    let durationMs: number | undefined = undefined;
                    const eventWithDuration = currentThinkingSlice.find(e => e.durationMs !== undefined && e.durationMs > 0);
                    if (eventWithDuration) {
                        durationMs = eventWithDuration.durationMs;
                    } else {
                        const firstEvent = currentThinkingSlice[0];
                        const lastEvent = currentThinkingSlice[currentThinkingSlice.length - 1];
                        const sliceStart = firstEvent?.timestamp;
                        let sliceEnd = nextNode?.timestamp;
                        if (!sliceEnd && lastEvent?.timestamp && sliceStart && lastEvent.timestamp > sliceStart) {
                            sliceEnd = lastEvent.timestamp;
                        }

                        if (!options.isHistory && sliceStart) {
                            const end = sliceEnd || thinkingEndTime || (!forceActive ? Date.now() : undefined);
                            if (end && end >= sliceStart) {
                                durationMs = end - sliceStart;
                            }
                        } else if (sliceStart && sliceEnd && sliceEnd > sliceStart) {
                            durationMs = sliceEnd - sliceStart;
                        } else if (totalThinkingBlocks === 1 && historyDurationMs) {
                            durationMs = historyDurationMs;
                        }
                    }

                    const shouldAttachMenu = isRoot && !hasAttachedToolMenu;
                    if (shouldAttachMenu) {
                        hasAttachedToolMenu = true;
                    }

                    result.push(
                        <ThinkingWidget 
                            key={key} 
                            events={[...currentThinkingSlice]} 
                            isActive={forceActive}
                            isHistory={options.isHistory}
                            durationMs={durationMs}
                            startTime={forceActive ? (currentThinkingSlice[0]?.timestamp || thinkingStartTime || liveStartTime || undefined) : undefined}
                            status={forceActive ? (liveStatus || undefined) : undefined}
                            toolMenu={shouldAttachMenu ? toolMenu : null}
                            narratorLine={forceActive ? (narratorLine || undefined) : undefined}
                            streamingRef={forceActive ? options.streamingRef : undefined}
                        />
                    );
                    currentThinkingSlice = [];
                    currentThinkingStartIdx = null;
                    currentToolStackTracker = null;
                }
            };

            nodes.forEach((node, idx) => {
                const isLastNode = idx === nodes.length - 1;

                if (node.type === 'speech') {
                    flushThinking(false, node);
                    result.push(<MarkdownMessage key={`speech-${idx}`} content={node.text || ''} sources={sources} />);
                    currentToolStackTracker = null;
                } else if (node.type === 'tool_start') {
                    flushThinking(false, node);
                    
                    const correlationId = node.correlation_id || `temp-${idx}`;
                    const ytDisplay = resolveYouTubeDisplay(node);
                    const mapDisplay = resolveMapDisplay(node);

                    // Inline YouTube player — prominent card, no tool bubble clutter.
                    if (ytDisplay) {
                        result.push(
                            <YouTubePlayerCard
                                key={`yt-inline-${correlationId}`}
                                id={ytDisplay.id}
                                title={ytDisplay.title}
                            />
                        );
                        currentToolStackTracker = null;
                        return;
                    }

                    if (mapDisplay) {
                        result.push(
                            <PlacesMapCard
                                key={`map-inline-${correlationId}`}
                                view={mapDisplay}
                            />
                        );
                        currentToolStackTracker = null;
                        return;
                    }

                    if (node.name === 'show_youtube_video' && node.success !== true) {
                        const pendingTitle = (node.params?.title as string) || 'YouTube video';
                        result.push(
                            <div key={`yt-pending-${correlationId}`} className="youtube-display-pending">
                                Loading: {pendingTitle}
                            </div>
                        );
                        currentToolStackTracker = null;
                        return;
                    }

                    if (node.name === 'show_map' && node.success !== true) {
                        const pendingTitle = (node.params?.title as string) || 'Map';
                        result.push(
                            <div key={`map-pending-${correlationId}`} className="places-map-pending">
                                Loading map: {pendingTitle}
                            </div>
                        );
                        currentToolStackTracker = null;
                        return;
                    }

                    const ui = resolveToolUI(node);
                    const category = ui.category;

                    // Recursive depth injection: Only pass active status deeper if this is the active tail
                    const passActiveDeep = isLastNode && options.isActive;
                    
                    let nestedChildren: React.ReactNode = undefined;
                    // We also pass down if this is a delegation tool OR it already has items!
                    const isDelegation = category === 'delegation';
                    if (node.children && (node.children.length > 0 || (isDelegation && passActiveDeep))) {
                         const childElements = renderNodes(node.children || [], {
                             isHistory: options.isHistory,
                             isActive: passActiveDeep,
                             streamingRef: options.streamingRef,
                             isRoot: false,
                         });
                         nestedChildren = <>{childElements}</>;
                    }

                    const itemStatus: StackedToolItem['status'] = node.awaiting_user
                        ? 'awaiting'
                        : node.success === true
                            ? 'success'
                            : node.success === false
                                ? 'error'
                                : 'pending';

                    // Tool stacking logic from Phase 1 preserved
                    if (currentToolStackTracker && currentToolStackTracker.category === category) {
                        const targetIdx = currentToolStackTracker.blockIdx;
                        const existingElement = result[targetIdx] as React.ReactElement<{
                            items: StackedToolItem[];
                        }> | undefined;
                        
                        if (existingElement && existingElement.props) {
                            const updatedItems = [
                                ...existingElement.props.items,
                                { correlationId, startEvent: node, status: itemStatus, nestedChildren }
                            ];
                            result[targetIdx] = React.cloneElement(existingElement, { items: updatedItems });
                        }
                    } else {
                        const blockIdx = result.length;
                        // Include blockIdx in key to guarantee uniqueness when the same tool
                        // is called multiple times non-consecutively (same correlationId would
                        // otherwise produce a duplicate key and break React reconciliation).
                        result.push(
                            <ToolStackBubble
                                key={`stack-${correlationId}-${blockIdx}`}
                                category={category}
                                items={[{ correlationId, startEvent: node, status: itemStatus, nestedChildren }]}
                            />
                        );
                        currentToolStackTracker = { category, blockIdx };
                    }
                } else if (node.type === 'thought' || node.type === 'thinking' || node.type === 'reasoning' || node.type === 'model_loading') {
                    if (currentThinkingStartIdx === null) {
                        currentThinkingStartIdx = idx;
                    }
                    currentThinkingSlice.push(node);
                } else if (node.type === 'subagent_group') {
                    flushThinking(false, node);
                    const status: 'pending' | 'success' | 'error' =
                        node.success === true ? 'success' : node.success === false ? 'error' : 'pending';
                    // Only nest live thinking inside a still-running subagent; once Scout
                    // is Done, Planner thoughts belong at the root.
                    const passActiveDeep = isLastNode && options.isActive && status === 'pending';
                    const childElements = renderNodes(node.children || [], {
                        isHistory: options.isHistory,
                        isActive: passActiveDeep,
                        streamingRef: options.streamingRef,
                        isRoot: false,
                    });
                    const prompt = node.prompt || extractSubagentPrompt(node);
                    result.push(
                        <SubagentPanel
                            key={`subagent-${node.correlation_id || idx}`}
                            agentName={friendlySubagentName(node)}
                            displayText={node.display_text}
                            prompt={prompt}
                            report={extractScoutReport(node.name, node.result)}
                            status={status}
                        >
                            {childElements}
                        </SubagentPanel>
                    );
                    currentToolStackTracker = null;
                } else if (node.type === 'browser_progress' && node.browserProgress) {
                    flushThinking(false, node);
                    const progress = node.browserProgress;
                    result.push(<div key={`browser-progress-${progress.run_id}`} className="browser-progress" role="status" aria-live="polite">
                        <strong>Browser · {progress.status.replace('_', ' ')}</strong>
                        <div>{progress.milestone}</div>
                        {progress.status === 'running' && progress.subgoal && <div>{progress.subgoal}</div>}
                        {progress.reason && <div>{progress.reason}</div>}
                        <small>{progress.actions} actions · {progress.recoveries} recoveries</small>
                    </div>);
                    currentToolStackTracker = null;
                } else if (node.type === 'execution_plan' && node.plan) {
                    flushThinking(false, node);
                    const planIsActive = isLastNode && options.isActive;
                    result.push(
                        <PlanCard
                            key={`plan-${node.plan_id || idx}`}
                            plan={node.plan}
                            goal={node.goal}
                            gatherBrief={node.gather_brief}
                            isActive={planIsActive}
                            awaitingApproval={awaitingPlanApproval}
                            isHistory={options.isHistory}
                            onApprove={(planIsActive || awaitingPlanApproval) && onPlanApproval ? () => onPlanApproval(true) : undefined}
                            onCancel={(planIsActive || awaitingPlanApproval) && onPlanApproval ? () => onPlanApproval(false) : undefined}
                        />
                    );
                    currentToolStackTracker = null;
                } else {
                    // Flush thinking for turn_complete or other events
                    flushThinking(false, node);
                }
            });

            // Final tail verification
            if (currentThinkingSlice.length > 0) {
                flushThinking(options.isActive);
            } else if (options.isActive) {
                // Check if we should place the active placeholder at THIS array's tail.
                // We only want to do this if the array is empty OR the last node was NOT a delegation tool 
                // (which recursively would have handled it!).
                const lastNode = nodes.length > 0 ? nodes[nodes.length - 1] : null;
                // A still-pending subagent group renders its own active tail internally.
                // Once the group is Done/Failed, root-level thinking (e.g. Planner) must show here.
                const lastNodeIsPendingDelegation = Boolean(
                    lastNode && (
                        (lastNode.type === 'subagent_group' && lastNode.success !== true && lastNode.success !== false)
                        || (lastNode.type === 'tool_start'
                            && resolveToolUI(lastNode).category === 'delegation'
                            && lastNode.success !== true
                            && lastNode.success !== false)
                    )
                );

                if (!lastNodeIsPendingDelegation) {
                    const shouldAttachMenu = isRoot && !hasAttachedToolMenu;
                    if (shouldAttachMenu) {
                        hasAttachedToolMenu = true;
                    }

                    result.push(
                        <ThinkingWidget
                            key="active-thinking-block"
                            events={[]}
                            isActive={true}
                            startTime={thinkingStartTime || liveStartTime || undefined}
                            status={liveStatus || undefined}
                            toolMenu={shouldAttachMenu ? toolMenu : null}
                            narratorLine={narratorLine || undefined}
                            streamingRef={options.streamingRef}
                            isHistory={options.isHistory}
                        />
                    );
                }
            }

            return result;
        };

        const roots = buildTree(treeEvents || []);
        const renderedBlocks = renderNodes(roots, { isHistory, isActive, streamingRef, isRoot: true });

        if (roots.length === 0 && toolMenu && !isActive) {
            renderedBlocks.unshift(
                <ThinkingWidget
                    key="history-tool-menu"
                    events={[]}
                    isHistory={isHistory}
                    isActive={false}
                    toolMenu={toolMenu}
                />
            );
        }

        const hasSpeechEvents = normalizedEvents?.some(e => e.type === 'speech');
        if (!hasSpeechEvents && text) {
            renderedBlocks.push(<MarkdownMessage key="fallback-text" content={text} sources={sources} />);
        }

        return renderedBlocks;
    }, [events, text, isHistory, isActive, awaitingPlanApproval, streamingRef, onPlanApproval, thinkingStartTime, thinkingEndTime, liveStatus, narratorLine, liveStartTime, historyDurationMs, toolMenu, sources]);

    return <>{blocks}</>;
});

