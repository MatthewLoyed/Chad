import { memo, useMemo } from 'react';
import { Check, AlertTriangle, Loader2, ChevronDown, ChevronRight, Pause } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useState } from 'react';
import type { ThinkingEvent } from '../../store';
import { resolveToolUI, toPastTenseToolTitle } from '../../utils/tool-ui';
import CollapseRail from '../common/CollapseRail';
import './ToolBubble.css';

const OUTPUT_PREVIEW_CHARS = 400;

function formatDetailText(value: unknown): string {
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function isInternalApprovalResult(result: unknown): boolean {
    if (typeof result !== 'string') return false;
    return result.includes('approval_token') || result.includes('_pending_approval');
}

function TruncatedDetailBlock({
    text,
    className,
}: {
    text: string;
    className?: string;
}) {
    const [showFull, setShowFull] = useState(false);
    const isLong = text.length > OUTPUT_PREVIEW_CHARS;
    const display = !showFull && isLong ? `${text.slice(0, OUTPUT_PREVIEW_CHARS)}…` : text;

    return (
        <div className="tool-bubble__details-block">
            <pre className={className}>{display}</pre>
            {isLong && (
                <button
                    type="button"
                    className="tool-bubble__show-more"
                    onClick={(e) => {
                        e.stopPropagation();
                        setShowFull((prev) => !prev);
                    }}
                >
                    {showFull ? 'Show less' : 'Show more'}
                </button>
            )}
        </div>
    );
}

interface ToolBubbleProps {
    event: ThinkingEvent;
    status: 'pending' | 'success' | 'error' | 'awaiting';
    children?: React.ReactNode;
}

function ToolBubble({ event, status, children }: ToolBubbleProps) {
    const hasChildren = !!children;
    
    const cleanParams = (params?: Record<string, unknown>) => {
        if (!params) return null;
        const clean: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(params)) {
            if (!k.startsWith('_') && v !== null && v !== '') {
                clean[k] = v;
            }
        }
        return Object.keys(clean).length > 0 ? clean : null;
    };

    const paramsToDisplay = useMemo(() => cleanParams(event.params), [event.params]);
    const hasDetails = !!paramsToDisplay || !!event.result;
    const isExpandable = hasChildren || hasDetails;

    const [prevStatus, setPrevStatus] = useState(status);
    const [prevExpandable, setPrevExpandable] = useState(isExpandable);
    const [isExpanded, setIsExpanded] = useState(status === 'pending');

    if (status !== prevStatus || isExpandable !== prevExpandable) {
        setPrevStatus(status);
        setPrevExpandable(isExpandable);
        if (status === 'pending' && isExpandable) {
            setIsExpanded(true);
        } else if (status !== 'pending' && status !== prevStatus) {
            setIsExpanded(false);
        }
    }

    const ui = useMemo(() => resolveToolUI(event), [event]);

    const label = useMemo(() => {
        if (status === 'pending') return ui.title;
        if (status === 'awaiting') return ui.title;
        if (status === 'success') {
            return toPastTenseToolTitle(ui.title);
        }
        if (ui.title.startsWith('Running ')) {
            return `Failed: ${ui.title.replace('Running ', '')}`;
        }
        return `Failed: ${ui.title.replace('...', '')}`;
    }, [ui.title, status]);

    return (
        <motion.div 
            className={`tool-bubble tool-bubble--${status} tool-bubble--cat-${ui.category} ${isExpandable ? 'tool-bubble--has-children' : ''}`}
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            layout
        >
            <div 
                className={`tool-bubble__content ${isExpandable ? 'tool-bubble__header-clickable' : ''}`}
                onClick={isExpandable ? () => setIsExpanded(!isExpanded) : undefined}
            >
                {/* Recursive Chevron */}
                {isExpandable && (
                    <div className="tool-bubble__chevron">
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </div>
                )}

                {/* 1. App Logo / Category Icon */}
                <div className="tool-bubble__icon-container">
                    <div className="tool-bubble__icon" style={{ color: ui.color }}>
                        {ui.icon}
                    </div>
                    {status === 'pending' && (
                        <div className="tool-bubble__spinner">
                            <Loader2 size={12} className="spin-slow" />
                        </div>
                    )}
                </div>

                {/* 2. Dynamic Label & Status */}
                <div className="tool-bubble__text">
                    <span className="tool-bubble__label">
                        {label}
                    </span>
                    <AnimatePresence mode="wait">
                        {status === 'pending' && (
                            <motion.span 
                                key="working"
                                className="tool-bubble__status"
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 0.6 }}
                                exit={{ opacity: 0 }}
                            >
                                Working...
                            </motion.span>
                        )}
                        {status === 'awaiting' && (
                            <motion.span
                                key="awaiting"
                                className="tool-bubble__status tool-bubble__status--awaiting"
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 0.85 }}
                                exit={{ opacity: 0 }}
                            >
                                Waiting for your answer
                            </motion.span>
                        )}
                    </AnimatePresence>
                </div>

                {/* 3. Result Indicator */}
                <div className="tool-bubble__indicator">
                    {status === 'success' ? (
                        <motion.div
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            transition={{ type: 'spring', stiffness: 600, damping: 12 }}
                            className="indicator-success"
                        >
                            <Check size={14} strokeWidth={3} />
                        </motion.div>
                    ) : status === 'awaiting' ? (
                        <motion.div
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            transition={{ type: 'spring', stiffness: 600, damping: 14 }}
                            className="indicator-awaiting"
                        >
                            <Pause size={14} />
                        </motion.div>
                    ) : status === 'error' ? (
                        <motion.div
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            transition={{ type: 'spring', stiffness: 600, damping: 14 }}
                            className="indicator-error"
                        >
                            <AlertTriangle size={14} />
                        </motion.div>
                    ) : null}
                </div>
            </div>

            {/* Nested Sub-Stream Content & Arguments/Logs Details */}
            <AnimatePresence>
                {isExpanded && isExpandable && (
                    <motion.div
                        className="tool-bubble__nested-children"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2 }}
                    >
                        <CollapseRail
                            className="tool-bubble__nested-content-inner"
                            onCollapse={() => setIsExpanded(false)}
                            label="Collapse tool details"
                        >
                            {/* Arguments Section */}
                            {paramsToDisplay && (
                                <div className="tool-bubble__details-section">
                                    <div className="tool-bubble__details-label">Arguments:</div>
                                    <TruncatedDetailBlock
                                        text={JSON.stringify(paramsToDisplay, null, 2)}
                                        className="tool-bubble__details-code"
                                    />
                                </div>
                            )}
                            
                            {/* Console Output Section (Skip if failed but error detail handles it, or if internal approval payload) */}
                            {event.result && !isInternalApprovalResult(event.result) && (
                                <div className="tool-bubble__details-section">
                                    <div className="tool-bubble__details-label">
                                        {status === 'error' ? 'Error Detail:' : 'Console Output:'}
                                    </div>
                                    <TruncatedDetailBlock
                                        text={formatDetailText(event.result)}
                                        className={`tool-bubble__details-code ${status === 'error' ? 'tool-bubble__details-code--error' : ''}`}
                                    />
                                </div>
                            )}

                            {/* Nested children */}
                            {hasChildren && children}
                        </CollapseRail>
                    </motion.div>
                )}
            </AnimatePresence>
        </motion.div>
    );
}

export default memo(ToolBubble);
