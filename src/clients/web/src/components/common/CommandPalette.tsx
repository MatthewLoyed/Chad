import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
    MessageSquarePlus,
    MessageSquare,
    ListOrdered,
    Rss,
    BookOpen,
    Kanban,
    LayoutDashboard,
    Zap,
    Settings,
    Brain,
    Search,
    Play,
    CornerDownLeft,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { Workspace } from '../../store';
import './CommandPalette.css';

interface CommandPaletteProps {
    onClose: () => void;
    /** Routed through App so navigating also closes the Settings page. */
    onNavigate: (workspace: Workspace) => void;
    onNewChat: () => void;
    onOpenSettings: () => void;
    onOpenMemory: () => void;
    onOpenSkills: () => void;
    onOpenSearch: () => void;
}

interface Command {
    id: string;
    label: string;
    hint?: string;
    icon: LucideIcon;
    group: 'Navigate' | 'Actions' | 'Run saved action';
    keywords?: string;
    run: () => void;
}

interface WorkflowLite {
    id: string;
    name: string;
}

/**
 * Command palette (Ctrl/Cmd+K). Collapses navigation and actions into one
 * keyboard-driven surface — the canonical "feels fast" pattern for desktop agent
 * apps. As workspaces multiply, users type instead of hunting the rail.
 *
 * Mounted only while open (see App), so state starts fresh each time — no
 * reset-on-open effect needed.
 */
export default function CommandPalette({
    onClose,
    onNavigate,
    onNewChat,
    onOpenSettings,
    onOpenMemory,
    onOpenSkills,
    onOpenSearch,
}: CommandPaletteProps) {
    const [query, setQuery] = useState('');
    const [activeIdx, setActiveIdx] = useState(0);
    const [routines, setRoutines] = useState<WorkflowLite[]>([]);
    const inputRef = useRef<HTMLInputElement>(null);
    const listRef = useRef<HTMLDivElement>(null);

    const goWorkspace = useCallback(
        (w: Workspace) => {
            onNavigate(w);
            onClose();
        },
        [onNavigate, onClose]
    );

    // Load workflows for "Run workflow" commands (external system read — runs once on mount).
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const getter = window.pywebview?.api?.get_routines ?? window.pywebview?.api?.get_workflows;
                const result = await getter?.();
                if (!cancelled && Array.isArray(result)) {
                    setRoutines((result as WorkflowLite[]).map((w) => ({ id: w.id, name: w.name })));
                }
            } catch {
                /* offline preview — no workflows */
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const commands = useMemo<Command[]>(() => {
        const base: Command[] = [
            { id: 'nav-feed', label: 'Go to Feed', icon: Rss, group: 'Navigate', keywords: 'activity updates', run: () => goWorkspace('feed') },
            { id: 'nav-chat', label: 'Go to Chat', icon: MessageSquare, group: 'Navigate', run: () => goWorkspace('chat') },
            { id: 'nav-routines', label: 'Go to Saved actions', icon: ListOrdered, group: 'Navigate', keywords: 'routine workflow macro', run: () => goWorkspace('routines') },
            { id: 'nav-board', label: 'Go to Board', icon: Kanban, group: 'Navigate', keywords: 'kanban tasks', run: () => goWorkspace('board') },
            { id: 'nav-dashboard', label: 'Go to Dashboard', icon: LayoutDashboard, group: 'Navigate', keywords: 'automation status feed', run: () => goWorkspace('dashboard') },
            { id: 'nav-automations', label: 'Go to Automations', icon: Zap, group: 'Navigate', keywords: 'autonomous heartbeat cron webhooks scheduled', run: () => goWorkspace('automations') },
            { id: 'act-new-chat', label: 'New chat', hint: 'Start fresh', icon: MessageSquarePlus, group: 'Actions', run: () => { onNewChat(); onClose(); } },
            { id: 'act-search', label: 'Search chats', icon: Search, group: 'Actions', keywords: 'find history', run: () => { onClose(); onOpenSearch(); } },
            { id: 'act-settings', label: 'Open Settings', icon: Settings, group: 'Actions', keywords: 'preferences audio model mcp', run: () => { onClose(); onOpenSettings(); } },
            { id: 'act-skills', label: 'Open Skills', icon: BookOpen, group: 'Actions', keywords: 'expertise guides catalog settings', run: () => { onClose(); onOpenSkills(); } },
            { id: 'act-memory', label: 'Open Memory', icon: Brain, group: 'Actions', keywords: 'settings context', run: () => { onClose(); onOpenMemory(); } },
        ];
        const routineCommands: Command[] = routines.map((w) => ({
            id: `run-${w.id}`,
            label: w.name,
            hint: 'Run saved action',
            icon: Play,
            group: 'Run saved action' as const,
            keywords: 'run routine automation',
            run: () => {
                window.pywebview?.api?.run_workflow?.(w.id);
                onNavigate('routines');
                onClose();
            },
        }));
        return [...base, ...routineCommands];
    }, [routines, goWorkspace, onClose, onNewChat, onOpenSearch, onOpenSettings, onOpenSkills, onOpenMemory, onNavigate]);

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return commands;
        return commands.filter((c) =>
            (c.label + ' ' + (c.keywords || '') + ' ' + c.group).toLowerCase().includes(q)
        );
    }, [commands, query]);

    // Clamp the active index during render so a shrinking filter never points past the end.
    const clampedIdx = Math.min(activeIdx, Math.max(0, filtered.length - 1));

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIdx(Math.min(clampedIdx + 1, filtered.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIdx(Math.max(clampedIdx - 1, 0));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            filtered[clampedIdx]?.run();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            onClose();
        }
    };

    // Scroll the active item into view.
    useEffect(() => {
        const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${clampedIdx}"]`);
        el?.scrollIntoView({ block: 'nearest' });
    }, [clampedIdx]);

    // Render groups in order, tracking a flat index for keyboard nav.
    let flatIdx = -1;
    const groups: Command['group'][] = ['Navigate', 'Actions', 'Run saved action'];

    return (
        <motion.div
            className="cmdk-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            onClick={onClose}
        >
            <motion.div
                className="cmdk"
                initial={{ opacity: 0, scale: 0.97, y: -8 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.97, y: -8 }}
                transition={{ type: 'spring', stiffness: 340, damping: 28 }}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="cmdk__search">
                    <Search size={17} className="cmdk__search-icon" />
                    <input
                        ref={inputRef}
                        className="cmdk__input"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Search workspaces and actions…"
                        autoFocus
                    />
                    <kbd className="cmdk__esc">esc</kbd>
                </div>

                <div className="cmdk__list" ref={listRef}>
                    {filtered.length === 0 ? (
                        <div className="cmdk__empty">No matches for “{query}”</div>
                    ) : (
                        groups.map((group) => {
                            const items = filtered.filter((c) => c.group === group);
                            if (items.length === 0) return null;
                            return (
                                <div key={group} className="cmdk__group">
                                    <div className="cmdk__group-label">{group}</div>
                                    {items.map((cmd) => {
                                        flatIdx += 1;
                                        const idx = flatIdx;
                                        const Icon = cmd.icon;
                                        return (
                                            <button
                                                key={cmd.id}
                                                type="button"
                                                data-idx={idx}
                                                className={`cmdk__item ${idx === clampedIdx ? 'is-active' : ''}`}
                                                onMouseMove={() => setActiveIdx(idx)}
                                                onClick={() => cmd.run()}
                                            >
                                                <Icon size={16} className="cmdk__item-icon" />
                                                <span className="cmdk__item-label">{cmd.label}</span>
                                                {cmd.hint && <span className="cmdk__item-hint">{cmd.hint}</span>}
                                                {idx === clampedIdx && (
                                                    <CornerDownLeft size={13} className="cmdk__item-enter" />
                                                )}
                                            </button>
                                        );
                                    })}
                                </div>
                            );
                        })
                    )}
                </div>
            </motion.div>
        </motion.div>
    );
}
