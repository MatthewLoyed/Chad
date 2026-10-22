import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, Check, AlertTriangle, Brain, Terminal, Copy } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import CollapseRail from '../common/CollapseRail';
import './SubagentPanel.css';

export type SubagentStatus = 'pending' | 'success' | 'error';

export interface SubagentPanelProps {
    /** Friendly subagent label, e.g. "Research Agent". */
    agentName: string;
    /** Optional richer launch description from the delegation tool's display_text. */
    displayText?: string;
    /** Prompt or instructions the subagent received from supervisor. */
    prompt?: string;
    report?: string;
    status: SubagentStatus;
    /** Optional default expansion state (useful for tests and stories). */
    defaultExpanded?: boolean;
    /** The recursively-rendered subagent body (its own thoughts + tool calls). */
    children?: React.ReactNode;
}

const STATUS_LABEL: Record<SubagentStatus, string> = {
    pending: 'Working…',
    success: 'Done',
    error: 'Failed',
};

/**
 * A self-contained, collapsible panel for one subagent invocation. It isolates a
 * delegated specialist's thoughts and tool calls from the main agent's stream — the
 * Claude-Code "Task" dropdown experience. Collapsed by default; expanding reveals the
 * prompt received from supervisor and the subagent's body (passed as `children`).
 */
export default function SubagentPanel({
    agentName,
    displayText,
    prompt,
    report,
    status,
    defaultExpanded = false,
    children,
}: SubagentPanelProps) {
    const [isExpanded, setIsExpanded] = useState(defaultExpanded);
    const [copied, setCopied] = useState(false);
    const [isPromptClamped, setIsPromptClamped] = useState(true);

    const cleanPrompt = prompt?.trim();
    const hasBody = React.Children.count(children) > 0 || Boolean(cleanPrompt) || Boolean(report);
    const isLongPrompt = Boolean(cleanPrompt && (cleanPrompt.length > 260 || cleanPrompt.split('\n').length > 5));

    // In the header, if displayText is missing or redundant ("Delegating to ... specialist"),
    // use the first line of the prompt as a preview so the user immediately knows the task.
    const effectiveDesc = (displayText && !displayText.toLowerCase().startsWith('delegating to '))
        ? displayText
        : cleanPrompt
        ? cleanPrompt.split('\n')[0]
        : displayText;

    const handleCopy = async () => {
        if (!cleanPrompt) return;
        try {
            if (navigator?.clipboard?.writeText) {
                await navigator.clipboard.writeText(cleanPrompt);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = cleanPrompt;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            }
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            // ignore clipboard errors
        }
    };

    return (
        <div className={`subagent-panel subagent-panel--${status}`}>
            <div
                className="subagent-panel__header"
                onClick={() => hasBody && setIsExpanded((v) => !v)}
                role={hasBody ? 'button' : undefined}
            >
                <div className="subagent-panel__icon">
                    <Brain size={15} />
                </div>

                <div className="subagent-panel__label">
                    <span className="subagent-panel__name">{agentName}</span>
                    {effectiveDesc && (
                        <span className="subagent-panel__desc" title={effectiveDesc}>
                            {effectiveDesc}
                        </span>
                    )}
                </div>

                <span className={`subagent-panel__status subagent-panel__status--${status}`}>
                    {STATUS_LABEL[status]}
                </span>

                <div className="subagent-panel__status-indicator">
                    {status === 'pending' ? <Loader2 className="spin-slow" size={13} /> :
                     status === 'error' ? <AlertTriangle size={13} className="alert-icon" /> :
                     <Check size={13} className="check-icon" />}
                </div>

                {hasBody && (
                    <div className="subagent-panel__chevron">
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </div>
                )}
            </div>

            <AnimatePresence>
                {isExpanded && hasBody && (
                    <motion.div
                        className="subagent-panel__body"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2, ease: 'easeInOut' }}
                    >
                        <CollapseRail
                            className="subagent-panel__body-inner"
                            onCollapse={() => setIsExpanded(false)}
                            label="Collapse subagent"
                        >
                            {cleanPrompt && (
                                <div className="subagent-panel__prompt-card" data-testid="subagent-prompt-card">
                                    <div className="subagent-panel__prompt-header">
                                        <div className="subagent-panel__prompt-tag">
                                            <Terminal size={12} className="subagent-panel__prompt-icon" />
                                            <span>Prompt</span>
                                        </div>
                                        <button
                                            type="button"
                                            className="subagent-panel__prompt-copy"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handleCopy();
                                            }}
                                            title="Copy prompt"
                                            aria-label="Copy prompt"
                                        >
                                            {copied ? <Check size={12} className="check-icon" /> : <Copy size={12} />}
                                            <span>{copied ? 'Copied' : 'Copy'}</span>
                                        </button>
                                    </div>
                                    <div
                                        className={`subagent-panel__prompt-text ${
                                            isPromptClamped && isLongPrompt ? 'subagent-panel__prompt-text--clamped' : ''
                                        }`}
                                    >
                                        {cleanPrompt}
                                    </div>
                                    {isLongPrompt && (
                                        <button
                                            type="button"
                                            className="subagent-panel__prompt-toggle"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setIsPromptClamped((v) => !v);
                                            }}
                                        >
                                            {isPromptClamped ? 'Show full prompt' : 'Show less'}
                                        </button>
                                    )}
                                </div>
                            )}
                            {children}
                            {report && (
                                <div className="subagent-panel__prompt">
                                    <div className="subagent-panel__prompt-label">Report</div>
                                    <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{report}</div>
                                </div>
                            )}
                        </CollapseRail>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}

