import { useState, useMemo } from 'react';
import { Check, X, Mail, Terminal, FileCode, Calendar, ShieldAlert, Copy } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './ApprovalCard.css';

interface ApprovalOption {
    id: string;
    label: string;
}

export interface ApprovalCardProps {
    prompt: string;
    decisionType: 'accept_decline' | 'choice';
    options?: ApprovalOption[];
    resolved?: boolean;
    userChoice?: string;
    onResolve?: (decision: string) => void;
    toolName?: string;
    params?: Record<string, unknown>;
}

type ActionCategory = 'email' | 'command' | 'file' | 'calendar' | 'generic';

function detectActionCategory(toolName?: string, params?: Record<string, unknown>): ActionCategory {
    if (!toolName) {
        if (params && ('to' in params && ('subject' in params || 'body' in params))) return 'email';
        if (params && ('command' in params || 'cmd' in params)) return 'command';
        if (params && ('file_path' in params || 'path' in params || 'filename' in params || 'target_file' in params)) return 'file';
        return 'generic';
    }
    const lower = toolName.toLowerCase();
    if (lower.includes('email') || lower.includes('gmail') || lower.includes('mail') || (params && 'to' in params && 'subject' in params)) {
        return 'email';
    }
    if (lower.includes('command') || lower.includes('bash') || lower.includes('terminal') || lower.includes('powershell') || lower.includes('run_sandboxed')) {
        return 'command';
    }
    if (lower.includes('file') || lower.includes('directory') || lower.includes('doc') || lower.includes('replace_file')) {
        return 'file';
    }
    if (lower.includes('calendar') || lower.includes('meeting') || lower.includes('event')) {
        return 'calendar';
    }
    return 'generic';
}

function formatKeyLabel(key: string): string {
    return key
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function ApprovalCard({
    prompt,
    decisionType,
    options,
    resolved,
    userChoice,
    onResolve,
    toolName,
    params,
}: ApprovalCardProps) {
    const [localChoice, setLocalChoice] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const choice = userChoice ?? localChoice;
    const isResolved = resolved || choice !== null;

    const category = useMemo(() => detectActionCategory(toolName, params), [toolName, params]);

    const handleChoice = (decision: string) => {
        if (isResolved) return;
        setLocalChoice(decision);
        onResolve?.(decision);
    };

    const handleCopy = (text: string) => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    // Card Icon & Title
    const { icon, title, approveLabel } = useMemo(() => {
        switch (category) {
            case 'email':
                if (toolName?.toLowerCase().includes('draft')) {
                    return {
                        icon: <Mail size={16} className="action-card__icon--email" />,
                        title: 'Create Email Draft',
                        approveLabel: 'Approve Draft',
                    };
                }
                return {
                    icon: <Mail size={16} className="action-card__icon--email" />,
                    title: 'Send Email',
                    approveLabel: 'Approve & Send Email',
                };
            case 'command':
                return {
                    icon: <Terminal size={16} className="action-card__icon--command" />,
                    title: 'Run Terminal Command',
                    approveLabel: 'Approve & Execute',
                };
            case 'file':
                return {
                    icon: <FileCode size={16} className="action-card__icon--file" />,
                    title: 'File Modification',
                    approveLabel: 'Approve & Apply',
                };
            case 'calendar':
                return {
                    icon: <Calendar size={16} className="action-card__icon--calendar" />,
                    title: 'Calendar Event',
                    approveLabel: 'Approve & Schedule',
                };
            default:
                return {
                    icon: <ShieldAlert size={16} className="action-card__icon--generic" />,
                    title: toolName ? formatKeyLabel(toolName) : 'Action Approval',
                    approveLabel: 'Approve Action',
                };
        }
    }, [category, toolName]);

    // Render specialized content body
    const renderContent = () => {
        if (!params || Object.keys(params).length === 0) {
            return (
                <div className="action-card__simple-prompt">
                    {prompt || 'Please confirm to continue.'}
                </div>
            );
        }

        if (category === 'email') {
            const to = String(params.to || params.recipient || '');
            const subject = String(params.subject || '(No Subject)');
            const body = String(params.body || params.content || params.message || '');

            return (
                <div className="action-card__email-body">
                    <div className="action-card__meta-section">
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">To:</span>
                            <span className="action-card__chip action-card__chip--email">{to}</span>
                        </div>
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">Subject:</span>
                            <span className="action-card__subject">{subject}</span>
                        </div>
                    </div>
                    {body && (
                        <div className="action-card__email-preview">
                            <div className="action-card__preview-label">Preview:</div>
                            <div className="action-card__preview-content">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                    {body}
                                </ReactMarkdown>
                            </div>
                        </div>
                    )}
                </div>
            );
        }

        if (category === 'command') {
            const cmd = String(params.command || params.cmd || params.script || '');
            const cwd = (params.cwd || params.working_dir || params.working_directory) as string | undefined;

            return (
                <div className="action-card__command-body">
                    {Boolean(cwd) && (
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">Directory:</span>
                            <code className="action-card__path">{String(cwd)}</code>
                        </div>
                    )}
                    <div className="action-card__code-container">
                        <pre className="action-card__code-block">{cmd}</pre>
                        <button
                            type="button"
                            className="action-card__code-copy"
                            onClick={() => handleCopy(cmd)}
                            title="Copy command"
                        >
                            {copied ? <Check size={11} /> : <Copy size={11} />}
                        </button>
                    </div>
                </div>
            );
        }

        if (category === 'file') {
            const filePath = String(params.file_path || params.path || params.filename || params.target_file || '');
            const content = String(params.content || params.code || params.patch || '');

            return (
                <div className="action-card__file-body">
                    {filePath && (
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">Target File:</span>
                            <code className="action-card__path">{filePath}</code>
                        </div>
                    )}
                    {content && (
                        <div className="action-card__code-container">
                            <pre className="action-card__code-block action-card__code-block--scroll">{content}</pre>
                        </div>
                    )}
                </div>
            );
        }

        if (category === 'calendar') {
            const titleText = String(params.title || params.summary || params.event_name || '');
            const when = String(params.start_time || params.when || params.date || '');
            const attendees = (params.attendees || params.participants) as string[] | string | undefined;

            return (
                <div className="action-card__calendar-body">
                    {titleText && (
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">Event:</span>
                            <span className="action-card__subject">{titleText}</span>
                        </div>
                    )}
                    {when && (
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">Time:</span>
                            <span className="action-card__chip">{when}</span>
                        </div>
                    )}
                    {Boolean(attendees) && (
                        <div className="action-card__meta-row">
                            <span className="action-card__meta-label">Attendees:</span>
                            <span className="action-card__chip">{Array.isArray(attendees) ? attendees.join(', ') : String(attendees)}</span>
                        </div>
                    )}
                </div>
            );
        }

        // Generic structured parameters
        return (
            <div className="action-card__generic-body">
                {prompt && <div className="action-card__generic-prompt">{prompt}</div>}
                <div className="action-card__param-list">
                    {Object.entries(params)
                        .filter(([k]) => !k.startsWith('_'))
                        .map(([k, v]) => (
                            <div key={k} className="action-card__param-row">
                                <span className="action-card__param-key">{formatKeyLabel(k)}:</span>
                                <span className="action-card__param-val">
                                    {typeof v === 'string' ? v : JSON.stringify(v)}
                                </span>
                            </div>
                        ))}
                </div>
            </div>
        );
    };

    return (
        <div className={`action-card action-card--${category}${isResolved ? ' action-card--resolved' : ''}`}>
            {/* Header */}
            <div className="action-card__header">
                <div className="action-card__header-left">
                    <span className="action-card__header-icon">{icon}</span>
                    <span className="action-card__header-title">{title}</span>
                </div>
                <div className="action-card__header-right">
                    {!isResolved ? (
                        <span className="action-card__badge action-card__badge--pending">
                            <span className="action-badge-dot" />
                            Awaiting Approval
                        </span>
                    ) : choice === 'declined' ? (
                        <span className="action-card__badge action-card__badge--declined">
                            <X size={11} />
                            Declined
                        </span>
                    ) : (
                        <span className="action-card__badge action-card__badge--approved">
                            <Check size={11} />
                            {category === 'email' ? 'Sent' : 'Approved'}
                        </span>
                    )}
                </div>
            </div>

            {/* Structured Content Body */}
            <div className="action-card__body">
                {renderContent()}
            </div>

            {/* Action Buttons Footer */}
            {!isResolved && decisionType === 'accept_decline' && (
                <div className="action-card__footer">
                    <button
                        type="button"
                        className="action-btn action-btn--decline"
                        onClick={() => handleChoice('declined')}
                    >
                        <X size={13} />
                        Decline
                    </button>
                    <button
                        type="button"
                        className="action-btn action-btn--approve"
                        onClick={() => handleChoice('approved')}
                    >
                        <Check size={13} />
                        {approveLabel}
                    </button>
                </div>
            )}

            {!isResolved && decisionType === 'choice' && (
                <div className="action-card__footer action-card__footer--choices">
                    {(options || []).map((opt) => (
                        <button
                            key={opt.id}
                            type="button"
                            className="action-option-btn"
                            onClick={() => handleChoice(opt.label)}
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>
            )}

            {isResolved && (
                <div className={`action-card__resolved-bar action-card__resolved-bar--${choice === 'declined' ? 'declined' : 'approved'}`}>
                    {choice === 'declined' ? (
                        <>
                            <X size={12} />
                            <span>Action declined</span>
                        </>
                    ) : (
                        <>
                            <Check size={12} />
                            <span>
                                {category === 'email' ? 'Email approved and sent' : choice === 'approved' ? 'Action approved' : choice}
                            </span>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
