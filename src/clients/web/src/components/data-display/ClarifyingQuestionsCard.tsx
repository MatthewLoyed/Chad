import { useMemo, useState } from 'react';
import { Check, ChevronLeft, ChevronRight } from 'lucide-react';
import './ClarifyingQuestionsCard.css';

import {
    OTHER_LABEL,
    isOtherLabel,
    formatAnswersSummary,
    parseResolvedQaPairs,
    type ClarifyingQuestion,
} from './clarifying-questions-utils';

// Re-exported so existing `import type { ClarifyingQuestion } from './ClarifyingQuestionsCard'`
// consumers keep working. Type-only, so Fast Refresh stays happy.
export type { ClarifyingOption, ClarifyingQuestion, ResolvedQaPair } from './clarifying-questions-utils';

interface ClarifyingQuestionsCardProps {
    title?: string;
    questions: ClarifyingQuestion[];
    resolved?: boolean;
    userChoice?: string;
    onResolve?: (decision: string, displayText: string) => void;
}

function ClarifyingQuestionsCard({
    questions,
    resolved,
    userChoice,
    onResolve,
}: ClarifyingQuestionsCardProps) {
    const safeQuestions = useMemo(
        () => questions.filter((q) => q.question && (q.options?.length ?? 0) >= 2),
        [questions],
    );

    const [activeIndex, setActiveIndex] = useState(0);
    const [selections, setSelections] = useState<Record<string, string[]>>({});
    const [otherText, setOtherText] = useState<Record<string, string>>({});
    const [localSubmitted, setLocalSubmitted] = useState(false);
    const [otherOpen, setOtherOpen] = useState(false);

    const isResolved = resolved || localSubmitted || !!userChoice;
    const current = safeQuestions[activeIndex];
    const isMulti = !!current?.multiSelect;
    const total = safeQuestions.length;

    const currentSelections = current ? selections[current.question] || [] : [];

    const toggleOption = (question: ClarifyingQuestion, label: string) => {
        if (isResolved) return;
        if (!question.multiSelect && label !== OTHER_LABEL) {
            setOtherOpen(false);
        }
        setSelections((prev) => {
            const existing = prev[question.question] || [];
            if (question.multiSelect) {
                const next = existing.includes(label)
                    ? existing.filter((l) => l !== label)
                    : [...existing, label];
                return { ...prev, [question.question]: next };
            }
            return { ...prev, [question.question]: [label] };
        });
    };

    const isQuestionComplete = (q: ClarifyingQuestion): boolean => {
        const picked = selections[q.question] || [];
        if (picked.length === 0) return false;
        const custom = (otherText[q.question] || '').trim();
        const hasOther = picked.includes(OTHER_LABEL);
        if (hasOther && !custom) {
            // Empty "Something else" doesn't count — still complete if other options are picked.
            return picked.some((label) => label !== OTHER_LABEL);
        }
        return true;
    };

    const allComplete = safeQuestions.every(isQuestionComplete);

    const buildAnswers = (): Record<string, string | string[]> => {
        const answers: Record<string, string | string[]> = {};
        for (const q of safeQuestions) {
            const picked = [...(selections[q.question] || [])];
            const custom = (otherText[q.question] || '').trim();
            const resolvedLabels = picked
                .map((label) => (label === OTHER_LABEL && custom ? custom : label))
                .filter((label) => label !== OTHER_LABEL);
            answers[q.question] = q.multiSelect ? resolvedLabels : resolvedLabels[0];
        }
        return answers;
    };

    const handleSubmit = () => {
        if (!allComplete || isResolved) return;
        const answers = buildAnswers();
        const payload = JSON.stringify({ answers });
        const displayText = formatAnswersSummary(safeQuestions, answers);
        setLocalSubmitted(true);
        onResolve?.(payload, displayText);
    };

    const handleContinue = () => {
        if (!current || !isQuestionComplete(current)) return;
        if (activeIndex < total - 1) {
            setActiveIndex((i) => i + 1);
            setOtherOpen(false);
        } else {
            handleSubmit();
        }
    };

    const openOther = () => {
        if (!current || isResolved) return;
        setOtherOpen(true);
        toggleOption(current, OTHER_LABEL);
    };

    if (safeQuestions.length === 0) {
        return null;
    }

    if (isResolved) {
        const pairs = parseResolvedQaPairs(
            safeQuestions,
            userChoice,
            userChoice ? undefined : buildAnswers(),
        );
        return (
            <div className="clarify-card clarify-card--resolved" role="status">
                {pairs.map((pair) => (
                    <div key={`${pair.question}:${pair.answer}`} className="clarify-card__qa">
                        {pair.question ? (
                            <p className="clarify-card__qa-question">{pair.question}</p>
                        ) : null}
                        <p className="clarify-card__qa-answer">{pair.answer}</p>
                    </div>
                ))}
            </div>
        );
    }

    const otherSelected = currentSelections.includes(OTHER_LABEL);
    const displayOptions = current.options.filter((opt) => !isOtherLabel(opt.label));

    return (
        <div className={`clarify-card${isMulti ? ' clarify-card--multi' : ''}`}>
            <div className="clarify-card__top">
                <h3 className="clarify-card__question">{current.question}</h3>
                {total > 1 && (
                    <div className="clarify-card__pager" aria-label={`Question ${activeIndex + 1} of ${total}`}>
                        <button
                            type="button"
                            className="clarify-card__pager-btn"
                            disabled={activeIndex === 0}
                            onClick={() => {
                                setActiveIndex((i) => Math.max(0, i - 1));
                                setOtherOpen(false);
                            }}
                            aria-label="Previous question"
                        >
                            <ChevronLeft size={14} />
                        </button>
                        <span className="clarify-card__pager-label">
                            {activeIndex + 1} of {total}
                        </span>
                        <button
                            type="button"
                            className="clarify-card__pager-btn"
                            disabled={activeIndex >= total - 1 || !isQuestionComplete(current)}
                            onClick={handleContinue}
                            aria-label="Next question"
                        >
                            <ChevronRight size={14} />
                        </button>
                    </div>
                )}
            </div>

            {isMulti && (
                <p className="clarify-card__hint">Select all that apply</p>
            )}

            <div
                className="clarify-card__options"
                role={isMulti ? 'group' : 'radiogroup'}
                aria-label={current.question}
            >
                {displayOptions.map((opt, idx) => {
                    const selected = currentSelections.includes(opt.label);
                    return (
                        <button
                            key={opt.label}
                            type="button"
                            role={isMulti ? 'checkbox' : 'radio'}
                            aria-checked={selected}
                            className={`clarify-card__option${selected ? ' clarify-card__option--selected' : ''}`}
                            onClick={() => toggleOption(current, opt.label)}
                        >
                            <span
                                className={`clarify-card__option-marker${isMulti ? ' clarify-card__option-marker--check' : ' clarify-card__option-marker--num'}`}
                                aria-hidden
                            >
                                {isMulti ? (selected ? <Check size={11} strokeWidth={3} /> : null) : idx + 1}
                            </span>
                            <span className="clarify-card__option-content">
                                <span className="clarify-card__option-label">{opt.label}</span>
                                {opt.description ? (
                                    <span className="clarify-card__option-desc">{opt.description}</span>
                                ) : null}
                            </span>
                        </button>
                    );
                })}
            </div>

            <div className="clarify-card__footer">
                <div className="clarify-card__other-row">
                    {!otherOpen && !otherSelected ? (
                        <button
                            type="button"
                            className="clarify-card__something-else"
                            onClick={openOther}
                        >
                            Something else
                        </button>
                    ) : (
                        <input
                            type="text"
                            className="clarify-card__other-input"
                            placeholder="Type your answer…"
                            value={otherText[current.question] || ''}
                            autoFocus
                            onChange={(e) => {
                                if (!otherSelected) toggleOption(current, OTHER_LABEL);
                                setOtherText((prev) => ({
                                    ...prev,
                                    [current.question]: e.target.value,
                                }));
                            }}
                        />
                    )}
                </div>

                {total > 1 && activeIndex < total - 1 ? (
                    <button
                        type="button"
                        className="clarify-card__submit"
                        disabled={!isQuestionComplete(current)}
                        onClick={handleContinue}
                    >
                        Continue
                        <ChevronRight size={14} />
                    </button>
                ) : (
                    <button
                        type="button"
                        className="clarify-card__submit"
                        disabled={!allComplete}
                        onClick={handleSubmit}
                    >
                        Submit
                    </button>
                )}
            </div>
        </div>
    );
}

export default ClarifyingQuestionsCard;
