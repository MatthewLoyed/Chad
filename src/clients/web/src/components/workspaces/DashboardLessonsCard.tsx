import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, Brain } from 'lucide-react';
import { useAppStore } from '../../store';
import type { ReflectionLesson } from '../../store';
import LessonReviewList from '../reflection/LessonReviewList';
import './DashboardLessonsCard.css';

export default function DashboardLessonsCard() {
    const lessons = useAppStore(state => state.reflectionLessons);
    const setReflectionLessons = useAppStore(state => state.setReflectionLessons);
    const removeReflectionLesson = useAppStore(state => state.removeReflectionLesson);
    const [expanded, setExpanded] = useState(true);

    const refreshLessons = useCallback(async () => {
        const api = window.pywebview?.api;
        if (!api || typeof api.get_reflection_lessons !== 'function') return;
        try {
            const result = await api.get_reflection_lessons(20);
            if (Array.isArray(result)) {
                setReflectionLessons(result as ReflectionLesson[]);
            }
        } catch (err) {
            console.error('Failed to load pending lessons', err);
        }
    }, [setReflectionLessons]);

    useEffect(() => {
        refreshLessons();
    }, [refreshLessons]);

    // Pop the card open whenever the lesson count changes and there's something to show.
    // Adjusting during render keeps it in the same commit as the data arriving, so the
    // card never paints collapsed for a frame before expanding.
    const [prevCount, setPrevCount] = useState(lessons.length);
    if (prevCount !== lessons.length) {
        setPrevCount(lessons.length);
        if (lessons.length > 0) setExpanded(true);
    }

    const onApprove = (id: string) => {
        removeReflectionLesson(id);
        window.pywebview?.api?.set_reflection_lesson_status?.(id, 'approved').catch(console.error);
    };

    const onDismiss = (id: string) => {
        removeReflectionLesson(id);
        window.pywebview?.api?.set_reflection_lesson_status?.(id, 'dismissed').catch(console.error);
    };

    const onAnswer = async (id: string, answer: string) => {
        const api = window.pywebview?.api;
        if (!api || typeof api.answer_reflection_question !== 'function') return;
        await api.answer_reflection_question(id, answer);
        removeReflectionLesson(id);
    };

    const pendingCount = lessons.length;

    return (
        <section className="dash-lessons-card">
            <button
                type="button"
                className="dash-lessons-card__header"
                onClick={() => setExpanded(v => !v)}
                aria-expanded={expanded}
            >
                <Brain size={15} className="dash-lessons-card__icon" />
                <span className="dash-lessons-card__title">Lessons</span>
                {pendingCount > 0 && (
                    <span className="dash-lessons-card__count">{pendingCount}</span>
                )}
                <ChevronDown
                    size={16}
                    className={`dash-lessons-card__chevron ${expanded ? 'dash-lessons-card__chevron--open' : ''}`}
                />
            </button>

            {expanded && (
                <div className="dash-lessons-card__body">
                    {pendingCount === 0 ? (
                        <p className="dash-lessons-card__empty">
                            Nothing to review. Chad will propose lessons here when reflection spots a pattern worth teaching.
                        </p>
                    ) : (
                        <>
                            <p className="dash-lessons-card__hint">
                                Approve to teach Chad; dismiss to ignore. Approved lessons appear in Settings → Memory.
                            </p>
                            <LessonReviewList
                                lessons={lessons}
                                onApprove={onApprove}
                                onDismiss={onDismiss}
                                onAnswer={onAnswer}
                            />
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
