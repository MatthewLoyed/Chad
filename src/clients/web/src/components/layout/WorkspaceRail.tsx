import { motion } from 'framer-motion';
import {
    MessageSquare,
    Rss,
    Kanban,
    LayoutDashboard,
    Zap,
    Command,
    Settings,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useActiveWorkspace } from '../../store';
import type { Workspace } from '../../store';
import ChadAvatarLive from '../common/chad-avatar';
import './WorkspaceRail.css';

interface WorkspaceRailProps {
    /** Routed through App so navigating can also close the Settings page. */
    onSelectWorkspace: (workspace: Workspace) => void;
    onCommandPalette?: () => void;
    onSettingsClick?: () => void;
    /** Highlights Settings in the rail while the Settings page is open. */
    settingsActive?: boolean;
}

interface RailItem {
    id: Workspace;
    label: string;
    icon: LucideIcon;
}

const WORKSPACE_ITEMS: RailItem[] = [
    { id: 'chat', label: 'Chat', icon: MessageSquare },
    { id: 'feed', label: 'Feed', icon: Rss },
    { id: 'automations', label: 'Automations', icon: Zap },
    { id: 'board', label: 'Board', icon: Kanban },
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
];

/**
 * Slim icon rail — the backbone of the workspace shell. The buddy anchors the
 * top as the app's identity; the middle switches primary workspaces; the bottom
 * holds global actions (command palette, settings). Every future destination is
 * one more entry in WORKSPACE_ITEMS — no relayout. See store.Workspace.
 */
export default function WorkspaceRail({
    onSelectWorkspace,
    onCommandPalette,
    onSettingsClick,
    settingsActive,
}: WorkspaceRailProps) {
    const activeWorkspace = useActiveWorkspace();

    return (
        <motion.nav
            className="ws-rail"
            initial={{ x: -64, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 240, damping: 26 }}
            aria-label="Primary"
        >
            <div className="ws-rail__buddy" title="Chad">
                <ChadAvatarLive size={52} />
            </div>

            <div className="ws-rail__group">
                {WORKSPACE_ITEMS.map((item) => {
                    const Icon = item.icon;
                    const isActive = !settingsActive && (activeWorkspace === item.id || (item.id === 'automations' && activeWorkspace === 'routines'));
                    return (
                        <button
                            key={item.id}
                            type="button"
                            className={`ws-rail__item ${isActive ? 'is-active' : ''}`}
                            onClick={() => onSelectWorkspace(item.id)}
                            title={item.label}
                            aria-label={item.label}
                            aria-current={isActive ? 'page' : undefined}
                        >
                            {isActive && (
                                <motion.span
                                    layoutId="ws-rail-active"
                                    className="ws-rail__active-pill"
                                    transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                                />
                            )}
                            <Icon size={19} />
                            <span className="ws-rail__tooltip">{item.label}</span>
                        </button>
                    );
                })}
            </div>

            <div className="ws-rail__spacer" />

            <div className="ws-rail__group ws-rail__group--bottom">
                <button
                    type="button"
                    className="ws-rail__item ws-rail__item--ghost"
                    onClick={onCommandPalette}
                    title="Command palette (Ctrl+K)"
                    aria-label="Command palette"
                >
                    <Command size={18} />
                    <span className="ws-rail__tooltip">Commands · Ctrl K</span>
                </button>
                <button
                    type="button"
                    className={`ws-rail__item ws-rail__item--ghost ${settingsActive ? 'is-active-ghost' : ''}`}
                    onClick={onSettingsClick}
                    title="Settings"
                    aria-label="Settings"
                >
                    <Settings size={18} />
                    <span className="ws-rail__tooltip">Settings</span>
                </button>
            </div>
        </motion.nav>
    );
}
