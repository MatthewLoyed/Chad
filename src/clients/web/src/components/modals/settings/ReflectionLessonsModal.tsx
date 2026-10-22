import { useEffect } from 'react';
import { Brain } from 'lucide-react';
import Modal from '../base/Modal';
import { useAppStore } from '../../../store';
import type { ReflectionLesson } from '../../../store';
import LessonReviewList from '../../reflection/LessonReviewList';
import './ReflectionLessonsModal.css';

interface ReflectionLessonsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function ReflectionLessonsModal({ isOpen, onClose }: ReflectionLessonsModalProps) {
    const lessons = useAppStore(state => state.reflectionLessons);
    const setReflectionLessons = useAppStore(state => state.setReflectionLessons);
    const removeReflectionLesson = useAppStore(state => state.removeReflectionLesson);

    useEffect(() => {
        if (isOpen && window.pywebview?.api) {
            const api = window.pywebview.api;
            if (typeof api.get_reflection_lessons === 'function') {
                api.get_reflection_lessons(20).then((result) => {
                    if (Array.isArray(result)) {
                        setReflectionLessons(result as ReflectionLesson[]);
                    }
                }).catch(console.error);
            }
        }
    }, [isOpen, setReflectionLessons]);

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

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="Reflection">
            {lessons.length === 0 ? (
                <div className="lessons-empty">
                    <Brain size={32} strokeWidth={1.5} />
                    <p className="lessons-empty__title">Nothing to review</p>
                    <p className="lessons-empty__sub">
                        Pending lessons appear on the Dashboard when Chad's reflection cycle spots recurring patterns.
                    </p>
                </div>
            ) : (
                <LessonReviewList
                    lessons={lessons}
                    onApprove={onApprove}
                    onDismiss={onDismiss}
                    onAnswer={onAnswer}
                />
            )}
        </Modal>
    );
}
