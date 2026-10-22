import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import './WorkspacePane.css';

interface WorkspacePaneProps {
    icon: LucideIcon;
    title: string;
    subtitle?: string;
    actions?: ReactNode;
    children: ReactNode;
    /** Removes the default body padding (e.g. the board manages its own scroll). */
    flush?: boolean;
}

/**
 * Shared chrome for a full-pane workspace: a header (icon + title + subtitle +
 * actions) and a scrollable body. Every non-chat workspace renders inside this so
 * they feel like one app, not a pile of modals. Entry is a soft fade/slide.
 */
export default function WorkspacePane({
    icon: Icon,
    title,
    subtitle,
    actions,
    children,
    flush,
}: WorkspacePaneProps) {
    return (
        <motion.section
            className="workspace-pane"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
        >
            <header className="workspace-pane__header">
                <div className="workspace-pane__heading">
                    <span className="workspace-pane__icon">
                        <Icon size={18} />
                    </span>
                    <div className="workspace-pane__titles">
                        <h1 className="workspace-pane__title">{title}</h1>
                        {subtitle && <p className="workspace-pane__subtitle">{subtitle}</p>}
                    </div>
                </div>
                {actions && <div className="workspace-pane__actions">{actions}</div>}
            </header>
            <div className={`workspace-pane__body ${flush ? 'is-flush' : ''}`} data-scrollable="true">
                {children}
            </div>
        </motion.section>
    );
}
