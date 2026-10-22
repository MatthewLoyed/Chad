import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import type { Variants } from 'framer-motion';
import './AppShell.css';

interface AppShellProps {
    nav?: ReactNode;
    sidebar: ReactNode;
    main?: ReactNode;
    artifactPanel?: ReactNode;
    uiMode: 'splash' | 'chat_only' | 'split_view';
    onToggleMain?: () => void;
}

const containerVariants: Variants = {
    hidden: { opacity: 0 },
    visible: {
        opacity: 1,
        transition: {
            staggerChildren: 0.1,
            delayChildren: 0.1,
        },
    },
};

export default function AppShell({ nav, sidebar, artifactPanel, uiMode }: AppShellProps) {
    const isSplash = uiMode === 'splash';

    return (
        <motion.div
            className={`app-shell mode-${isSplash ? 'splash' : 'chat_only'}${artifactPanel ? ' has-artifact-panel' : ''}`}
            variants={containerVariants}
            initial="hidden"
            animate="visible"
        >
            {/* New Nav Sidebar Section */}
            {nav && (
                <div className="app-shell__nav">
                    {nav}
                </div>
            )}

            <motion.aside
                className="app-shell__sidebar"
                style={{ flex: '1 1 auto', width: '100%', opacity: 1, minWidth: 0 }}
            >
                <div style={{ width: '100%', height: '100%', overflow: 'hidden' }}>
                    {sidebar}
                </div>
            </motion.aside>

            {artifactPanel}
        </motion.div>
    );
}
