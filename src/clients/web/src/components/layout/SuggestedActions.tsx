import { useMemo } from 'react';
import { motion } from 'framer-motion';
import './SuggestedActions.css';

interface SuggestedActionsProps {
    onSelect: (text: string) => void;
}

const morningActions = [
    { label: '☀️ Morning Briefing', prompt: 'Give me my morning briefing.' },
    { label: '📧 Unread Emails', prompt: 'Go through my unread email and summarize it, list anything important.' },
    { label: '✅ Today\'s Tasks', prompt: 'What tasks are on my Todoist for today?' },
    { label: '📚 Canvas Todos', prompt: 'What assignments do I have coming up in Canvas?' },
];

const afternoonActions = [
    { label: '📋 Status Check', prompt: 'What have I accomplished today and what\'s still pending?' },
    { label: '📧 Unread Emails', prompt: 'Summarize any unread emails I got since this morning.' },
    { label: '🔍 Deep Work', prompt: 'Help me focus — what\'s the single most important thing I should do right now?' },
    { label: '✅ Today\'s Tasks', prompt: 'What tasks are on my Todoist for today?' },
];

const eveningActions = [
    { label: '🌙 Day Summary', prompt: 'Give me a quick summary of what I accomplished today.' },
    { label: '📋 Plan Tomorrow', prompt: 'Help me plan and prioritize my tasks for tomorrow.' },
    { label: '📧 Evening Inbox', prompt: 'Summarize any important emails I received today.' },
    { label: '💡 Reflect', prompt: 'What should I be thinking about or preparing for the rest of this week?' },
];

export default function SuggestedActions({ onSelect }: SuggestedActionsProps) {
    const actions = useMemo(() => {
        const hour = new Date().getHours();
        if (hour < 12) return morningActions;
        if (hour < 17) return afternoonActions;
        return eveningActions;
    }, []);

    return (
        <div className="suggested-actions">
            {actions.map((action, index) => (
                <motion.button
                    key={index}
                    className="suggested-action-btn"
                    onClick={() => onSelect(action.prompt)}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: index * 0.05, duration: 0.2 }}
                    whileHover={{ scale: 1.03 }}
                    whileTap={{ scale: 0.96 }}
                >
                    {action.label}
                </motion.button>
            ))}
        </div>
    );
}
