import { motion } from 'framer-motion';
import { Settings, LayoutDashboard, Kanban, ListOrdered, Lightbulb, Zap, Brain } from 'lucide-react';
import { useAppStore } from '../../store';
import './ControlDock.css';

interface ControlDockProps {
    onSettingsClick: () => void;
    onDashboardClick: () => void;
    onKanbanClick: () => void;
    onRoutinesClick: () => void;
    onAutomationsClick: () => void;
    onSuggestionsClick: () => void;
    onReflectionLessonsClick: () => void;
}

const buttonVariants = {
    initial: { scale: 1 },
    hover: { scale: 1.1 },
    tap: { scale: 0.95 },
};

export default function ControlDock({
    onSettingsClick,
    onDashboardClick,
    onKanbanClick,
    onRoutinesClick,
    onAutomationsClick,
    onSuggestionsClick,
    onReflectionLessonsClick,
}: ControlDockProps) {
    const suggestionCount = useAppStore(state => state.capabilitySuggestions.length);
    const reflectionLessonCount = useAppStore(
        state => state.reflectionLessons.filter(l => l.status === 'pending').length
    );

    const buttons = [
        { icon: Settings, onClick: onSettingsClick, title: 'Settings', badge: 0 },
        { icon: Lightbulb, onClick: onSuggestionsClick, title: "Chad's Recommendations", badge: suggestionCount },
        { icon: Brain, onClick: onReflectionLessonsClick, title: 'Reflection', badge: reflectionLessonCount },
        { icon: LayoutDashboard, onClick: onDashboardClick, title: 'Dashboard', badge: 0 },
        { icon: Zap, onClick: onAutomationsClick, title: 'Automations', badge: 0 },
        { icon: Kanban, onClick: onKanbanClick, title: 'Kanban Board', badge: 0 },
        { icon: ListOrdered, onClick: onRoutinesClick, title: 'Routines', badge: 0 },
    ];

    return (
        <div className="control-dock">
            {buttons.map(({ icon: Icon, onClick, title, badge }) => (
                <motion.button
                    key={title}
                    className="control-dock__btn"
                    onClick={(e) => {
                        e.stopPropagation();
                        onClick();
                    }}
                    title={title}
                    variants={buttonVariants}
                    initial="initial"
                    whileHover="hover"
                    whileTap="tap"
                >
                    <Icon size={18} strokeWidth={1.5} />
                    {badge > 0 && <span className="control-dock__badge">{badge}</span>}
                </motion.button>
            ))}
        </div>
    );
}
