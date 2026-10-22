import { useState } from 'react';
import { motion } from 'framer-motion';
import { Check, X, Brain, HelpCircle, Send } from 'lucide-react';
import type { ReflectionLesson } from '../../store';
import './LessonReviewList.css';

interface LessonReviewListProps {
    lessons: ReflectionLesson[];
    onApprove: (id: string) => void;
    onDismiss: (id: string) => void;
    onAnswer: (id: string, answer: string) => Promise<void>;
}

export default function LessonReviewList({
    lessons,
    onApprove,
    onDismiss,
    onAnswer,
}: LessonReviewListProps) {
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [submitting, setSubmitting] = useState<Record<string, boolean>>({});

    const submitAnswer = async (id: string) => {
        const answer = (answers[id] || '').trim();
        if (!answer) return;
        setSubmitting(prev => ({ ...prev, [id]: true }));
        try {
            await onAnswer(id, answer);
        } finally {
            setSubmitting(prev => ({ ...prev, [id]: false }));
        }
    };

    if (lessons.length === 0) {
        return null;
    }

    return (
        <div className="lessons-list">
            {lessons.map((l) => {
                if (l.kind === 'question') {
                    return (
                        <div key={l.id} className="lesson-card">
                            <div className="lesson-card__head">
                                <span className="lesson-card__badge lesson-card__badge--question">
                                    <HelpCircle size={12} strokeWidth={2} /> question
                                </span>
                            </div>
                            <p className="lesson-card__text">{l.text}</p>
                            {l.why && (
                                <p className="lesson-card__why">
                                    <span className="lesson-card__why-label">Why</span>
                                    {l.why}
                                </p>
                            )}
                            <div className="lesson-card__answer">
                                <input
                                    className="lesson-card__answer-input"
                                    placeholder="Your answer…"
                                    value={answers[l.id] || ''}
                                    onChange={(e) => setAnswers(prev => ({ ...prev, [l.id]: e.target.value }))}
                                    onKeyDown={(e) => { if (e.key === 'Enter') submitAnswer(l.id); }}
                                    disabled={!!submitting[l.id]}
                                />
                                <motion.button
                                    className="lesson-btn lesson-btn--accept"
                                    style={{ flex: '0 0 auto' }}
                                    onClick={() => submitAnswer(l.id)}
                                    whileHover={{ scale: 1.03 }}
                                    whileTap={{ scale: 0.97 }}
                                    disabled={!!submitting[l.id]}
                                >
                                    <Send size={14} strokeWidth={2.5} />
                                </motion.button>
                            </div>
                        </div>
                    );
                }

                return (
                    <div key={l.id} className="lesson-card">
                        <div className="lesson-card__head">
                            <span className={`lesson-card__badge lesson-card__badge--${l.scope === 'user' ? 'user' : 'tools'}`}>
                                <Brain size={12} strokeWidth={2} />
                                {l.scope === 'user' ? 'knowledge graph' : 'persona'}
                            </span>
                        </div>

                        <p className="lesson-card__text">{l.text}</p>

                        {l.evidence && (
                            <p className="lesson-card__why">
                                <span className="lesson-card__why-label">Evidence</span>
                                {l.evidence}
                            </p>
                        )}

                        <div className="lesson-card__meta">
                            <span className="lesson-card__metric" title="Model confidence">
                                {Math.round((l.confidence ?? 0) * 100)}% confidence
                            </span>
                        </div>

                        <div className="lesson-card__actions">
                            <motion.button
                                className="lesson-btn lesson-btn--accept"
                                onClick={() => onApprove(l.id)}
                                whileHover={{ scale: 1.03 }}
                                whileTap={{ scale: 0.97 }}
                            >
                                <Check size={14} strokeWidth={2.5} /> Approve
                            </motion.button>
                            <motion.button
                                className="lesson-btn lesson-btn--dismiss"
                                onClick={() => onDismiss(l.id)}
                                whileHover={{ scale: 1.03 }}
                                whileTap={{ scale: 0.97 }}
                            >
                                <X size={14} strokeWidth={2.5} /> Dismiss
                            </motion.button>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
