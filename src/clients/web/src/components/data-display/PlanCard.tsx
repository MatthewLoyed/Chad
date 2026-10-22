import { memo, useState } from 'react';
import { ChevronDown, ChevronRight, Check, AlertTriangle, Loader2, Clock, ListTodo, Play, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import type { PlanStep } from '../../store';
import './PlanCard.css';

/**
 * A plan arrives either as a bare step array or wrapped in a `{ steps }` envelope,
 * depending on whether it came from the reducer turn or a stored history row.
 */
type PlanInput = PlanStep[] | { steps?: PlanStep[] } | null | undefined;

interface PlanCardProps {
    plan: PlanInput;
    goal?: string;
    gatherBrief?: string;
    isActive?: boolean;
    isHistory?: boolean;
    /** When true, show Proceed/Cancel even if the live turn is no longer "active". */
    awaitingApproval?: boolean;
    onApprove?: () => void;
    onCancel?: () => void;
}

function StepIcon({ status }: { status: PlanStep['status'] }) {
    switch (status) {
        case 'completed':
            return <Check size={12} className="plan-step__icon plan-step__icon--completed" />;
        case 'failed':
            return <AlertTriangle size={12} className="plan-step__icon plan-step__icon--failed" />;
        case 'running':
            return <Loader2 size={12} className="plan-step__icon plan-step__icon--running" />;
        default:
            return <Clock size={12} className="plan-step__icon plan-step__icon--pending" />;
    }
}

const PlanCard = memo(function PlanCard({
    plan,
    goal,
    gatherBrief,
    isActive = false,
    isHistory = false,
    awaitingApproval = false,
    onApprove,
    onCancel,
}: PlanCardProps) {
    const [isExpanded, setIsExpanded] = useState(!isHistory);
    const [approved, setApproved] = useState<boolean | null>(null);

    const steps: PlanStep[] = Array.isArray(plan)
        ? plan
        : (plan && Array.isArray(plan.steps) ? plan.steps : []);

    const completedCount = steps.filter(s => s.status === 'completed').length;
    const failedCount = steps.filter(s => s.status === 'failed').length;
    const runningStep = steps.find(s => s.status === 'running');

    const allPending = steps.every(s => !s.status || s.status === 'pending');
    const showApproval =
        (isActive || awaitingApproval)
        && allPending
        && (onApprove || onCancel)
        && approved === null;

    const aggStatus = failedCount > 0
        ? 'failed'
        : runningStep || isActive || awaitingApproval
            ? 'running'
            : completedCount === steps.length && steps.length > 0
                ? 'completed'
                : 'pending';

    return (
        <div className={`plan-card plan-card--${aggStatus}${isExpanded ? ' is-expanded' : ''}`}>
            <button
                type="button"
                className="plan-card__header"
                onClick={() => setIsExpanded(p => !p)}
                aria-expanded={isExpanded}
            >
                <span className="plan-card__header-icon">
                    <ListTodo size={14} />
                </span>
                <span className="plan-card__label">Execution Plan</span>

                {/* Inline progress pills */}
                <span className="plan-card__progress">
                    {steps.slice(0, 6).map((step, i) => (
                        <span
                            key={step.id || i}
                            className={`plan-pill plan-pill--${step.status ?? 'pending'}`}
                            title={step.description}
                        />
                    ))}
                    {steps.length > 6 && (
                        <span className="plan-card__overflow">+{steps.length - 6}</span>
                    )}
                </span>

                <span className="plan-card__count">
                    {completedCount}/{steps.length}
                </span>

                <span className="plan-card__chevron">
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </span>
            </button>

            <AnimatePresence initial={false}>
                {isExpanded && (
                    <motion.div
                        key="plan-body"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.18, ease: 'easeInOut' }}
                        className="plan-card__body"
                    >
                        <div className="plan-card__body-inner">
                            {gatherBrief && (
                                <div className="plan-card__gather-brief">
                                    <span className="plan-card__gather-label">Scout summary</span>
                                    <p className="plan-card__gather-text">{gatherBrief}</p>
                                </div>
                            )}
                            {goal && (
                                <p className="plan-card__goal">{goal}</p>
                            )}
                            <ol className="plan-card__steps">
                                {steps.map((step, i) => (
                                    <li
                                        key={step.id || i}
                                        className={`plan-step plan-step--${step.status ?? 'pending'}`}
                                    >
                                        <span className="plan-step__number">{i + 1}</span>
                                        <StepIcon status={step.status} />
                                        <span className="plan-step__description">{step.description}</span>
                                        {step.tools && step.tools.length > 0 && (
                                            <span className="plan-step__tools">
                                                {step.tools.map(t => (
                                                    <span key={t} className="plan-step__tool-chip">{t}</span>
                                                ))}
                                            </span>
                                        )}
                                    </li>
                                ))}
                            </ol>

                            {showApproval && (
                                <div className="plan-card__approval">
                                    <button
                                        type="button"
                                        className="plan-approval-btn plan-approval-btn--approve"
                                        onClick={() => { setApproved(true); onApprove?.(); }}
                                    >
                                        <Play size={13} />
                                        Proceed
                                    </button>
                                    <button
                                        type="button"
                                        className="plan-approval-btn plan-approval-btn--cancel"
                                        onClick={() => { setApproved(false); onCancel?.(); }}
                                    >
                                        <X size={13} />
                                        Cancel
                                    </button>
                                </div>
                            )}

                            {approved !== null && allPending && (
                                <div className={`plan-card__approval-result plan-card__approval-result--${approved ? 'approved' : 'cancelled'}`}>
                                    {approved ? <><Check size={13} /> Starting…</> : <><X size={13} /> Cancelled</>}
                                </div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
});

export default PlanCard;
