import { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Plus,
    Search,
    Folder,
    Shapes,
    MoreHorizontal,
    ChevronDown,
    Pin,
    Pencil,
    FolderInput,
    Trash2,
    PanelLeft,
    Loader2,
    X
} from 'lucide-react';
import { useAppStore } from '../../store';
import type { Conversation } from '../../store';
import { DeleteChatModal, MoveToProjectModal } from '../modals';
import type { WorkspaceProject } from '../modals/chat/MoveToProjectModal';
import './NavSidebar.css';

const NAV_WIDTH_STORAGE_KEY = 'chad.navSidebarWidth';
const DEFAULT_NAV_WIDTH = 260;
const MIN_NAV_WIDTH = 200;
/** Cap the chat history rail so the main pane stays dominant. */
const MAX_NAV_WIDTH_RATIO = 0.3;

function clampNavWidth(width: number): number {
    if (typeof window === 'undefined') {
        return Math.min(Math.max(width, MIN_NAV_WIDTH), Math.max(MIN_NAV_WIDTH, 480));
    }
    const max = Math.floor(window.innerWidth * MAX_NAV_WIDTH_RATIO);
    return Math.min(Math.max(width, MIN_NAV_WIDTH), Math.max(MIN_NAV_WIDTH, max));
}

function readStoredNavWidth(): number {
    try {
        const raw = localStorage.getItem(NAV_WIDTH_STORAGE_KEY);
        if (!raw) return DEFAULT_NAV_WIDTH;
        const parsed = Number.parseInt(raw, 10);
        if (!Number.isFinite(parsed)) return DEFAULT_NAV_WIDTH;
        return clampNavWidth(parsed);
    } catch {
        return DEFAULT_NAV_WIDTH;
    }
}

interface NavSidebarProps {
    /** Ordinary new chats use general memory; legacy domain routing stays compatible. */
    onNewChat?: (domainId?: string | null) => void;
    onChatSelect?: (id: string) => void;
    onLoadMore?: () => void;
    onArtifactsClick?: () => void;
    onSearchClick?: () => void;
}

type ActionMenuState = { chatId: string; chatTitle: string } | null;
type MoveProjectState = { chatId: string; chatTitle: string; projectPath?: string | null } | null;

function NavSidebarSkeleton({ count = 5 }: { count?: number }) {
    const widths = ['75%', '55%', '88%', '65%', '45%', '80%'];
    return (
        <div className="nav-sidebar-skeleton" aria-busy="true" aria-label="Loading list">
            {Array.from({ length: count }).map((_, i) => (
                <div key={i} className="nav-sidebar-skeleton__item">
                    <div className="nav-sidebar-skeleton__icon" />
                    <div
                        className="nav-sidebar-skeleton__line"
                        style={{ width: widths[i % widths.length] }}
                    />
                </div>
            ))}
        </div>
    );
}

export default function NavSidebar({
    onNewChat,
    onChatSelect,
    onLoadMore,
    onArtifactsClick,
    onSearchClick
}: NavSidebarProps) {
    const isCollapsed = useAppStore(state => state.isNavCollapsed);
    const setIsCollapsed = useAppStore(state => state.setIsNavCollapsed);
    const [navWidth, setNavWidth] = useState(readStoredNavWidth);
    const [isResizing, setIsResizing] = useState(false);
    const resizeStartX = useRef(0);
    const resizeStartWidth = useRef(DEFAULT_NAV_WIDTH);
    const navWidthRef = useRef(navWidth);
    navWidthRef.current = navWidth;
    const [deleteModal, setDeleteModal] = useState<{ isOpen: boolean; id: string | null; title: string | null }>({
        isOpen: false,
        id: null,
        title: null
    });
    const [actionMenu, setActionMenu] = useState<ActionMenuState>(null);
    const [moveProjectModal, setMoveProjectModal] = useState<MoveProjectState>(null);
    const [renamingId, setRenamingId] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState('');
    const menuRef = useRef<HTMLDivElement>(null);

    // Pull-to-refresh state for the recents list
    const [pullY, setPullY] = useState(0);
    const pullStartY = useRef(0);
    const isPulling = useRef(false);
    const PULL_THRESHOLD = 60;

    useEffect(() => {
        const onWindowResize = () => setNavWidth((w) => clampNavWidth(w));
        window.addEventListener('resize', onWindowResize);
        return () => window.removeEventListener('resize', onWindowResize);
    }, []);

    const persistNavWidth = useCallback((width: number) => {
        try {
            localStorage.setItem(NAV_WIDTH_STORAGE_KEY, String(width));
        } catch {
            /* private mode / quota — width still works for the session */
        }
    }, []);

    const handleResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
        if (isCollapsed || e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        resizeStartX.current = e.clientX;
        resizeStartWidth.current = navWidthRef.current;
        setIsResizing(true);
    };

    const handleResizePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
        if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
        const next = clampNavWidth(resizeStartWidth.current + (e.clientX - resizeStartX.current));
        navWidthRef.current = next;
        setNavWidth(next);
    };

    const handleResizePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
            e.currentTarget.releasePointerCapture(e.pointerId);
        }
        setIsResizing((was) => {
            if (was) persistNavWidth(navWidthRef.current);
            return false;
        });
    };

    useEffect(() => {
        if (!isResizing) return;
        const previousCursor = document.body.style.cursor;
        document.body.style.cursor = 'col-resize';
        return () => {
            document.body.style.cursor = previousCursor;
        };
    }, [isResizing]);

    // Projects nav section (#164) — a simple list backed by the same workspace
    // project folders used by "Move to Project"; selecting one filters Recents.
    const [projectsExpanded, setProjectsExpanded] = useState(false);
    const [projects, setProjects] = useState<WorkspaceProject[]>([]);
    const [projectsLoading, setProjectsLoading] = useState(false);
    const [projectsFetched, setProjectsFetched] = useState(false);
    const [selectedProjectPath, setSelectedProjectPath] = useState<string | null>(null);
    const [isCreatingProject, setIsCreatingProject] = useState(false);
    const [newProjectName, setNewProjectName] = useState('');
    const [renamingProjectPath, setRenamingProjectPath] = useState<string | null>(null);
    const [renameProjectValue, setRenameProjectValue] = useState('');

    // Store data
    const conversations = useAppStore(state => state.conversations);
    const activeConversationId = useAppStore(state => state.activeConversationId);
    const isHistoryLoading = useAppStore(state => state.isHistoryLoading);
    const hasMoreConversations = useAppStore(state => state.hasMoreConversations);
    const removeConversation = useAppStore(state => state.removeConversation);
    const updateConversation = useAppStore(state => state.updateConversation);
    const apiReady = useAppStore(state => state.apiReady);
    const appStatus = useAppStore(state => state.status);
    const isOffline = !apiReady || appStatus === 'initializing' || appStatus === 'error';
    // All conversations stay discoverable; existing domain metadata remains intact.
    const visibleConversations = conversations.filter((c) =>
        !selectedProjectPath || c.project_path === selectedProjectPath
    );

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
                setActionMenu(null);
            }
        };
        if (actionMenu) {
            document.addEventListener('mousedown', handleClickOutside);
        }
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [actionMenu]);

    const handleMoreClick = (e: React.MouseEvent, chat: Conversation) => {
        e.stopPropagation();
        if (actionMenu?.chatId === chat.id) {
            setActionMenu(null);
        } else {
            setActionMenu({ chatId: chat.id, chatTitle: chat.title });
        }
    };

    const closeActionMenu = () => setActionMenu(null);

    const handleRenameStart = (chat: Conversation) => {
        setRenamingId(chat.id);
        setRenameValue(chat.title);
        closeActionMenu();
    };

    const handleRenameSubmit = async (id: string) => {
        const trimmed = renameValue.trim();
        setRenamingId(null);
        if (!trimmed) return;

        try {
            const api = window.pywebview?.api;
            if (api && typeof api.rename_chat === 'function') {
                const success = await api.rename_chat(id, trimmed);
                if (success) {
                    updateConversation(id, { title: trimmed });
                }
            }
        } catch (err) {
            console.error('Failed to rename chat:', err);
        }
    };

    const handleRenameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>, id: string) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleRenameSubmit(id);
        } else if (e.key === 'Escape') {
            setRenamingId(null);
        }
    };

    const handlePinToggle = async (chat: Conversation) => {
        closeActionMenu();
        const nextPinned = !chat.is_pinned;
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.pin_chat === 'function') {
                const success = await api.pin_chat(chat.id, nextPinned);
                if (success) {
                    updateConversation(chat.id, { is_pinned: nextPinned });
                }
            }
        } catch (err) {
            console.error('Failed to pin chat:', err);
        }
    };

    const handleMoveToProjectStart = (chat: Conversation) => {
        closeActionMenu();
        setMoveProjectModal({
            chatId: chat.id,
            chatTitle: chat.title,
            projectPath: chat.project_path,
        });
    };

    const handleMoveToProjectSelect = async (projectPath: string | null) => {
        if (!moveProjectModal) return;
        const { chatId } = moveProjectModal;
        setMoveProjectModal(null);
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.move_chat_to_project === 'function') {
                const success = await api.move_chat_to_project(chatId, projectPath);
                if (success) {
                    updateConversation(chatId, { project_path: projectPath });
                }
            }
        } catch (err) {
            console.error('Failed to move chat to project:', err);
        }
    };

    const loadProjects = async () => {
        setProjectsLoading(true);
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.get_workspace_projects === 'function') {
                const res = await api.get_workspace_projects() as WorkspaceProject[];
                setProjects(Array.isArray(res) ? res : []);
            }
        } catch (err) {
            console.error('Failed to load workspace projects:', err);
        } finally {
            setProjectsLoading(false);
        }
    };

    const handleToggleProjects = () => {
        const next = !projectsExpanded;
        setProjectsExpanded(next);
        if (next && !projectsFetched) {
            setProjectsFetched(true);
            loadProjects();
        }
    };

    const handleSelectProject = (path: string) => {
        setSelectedProjectPath((prev) => (prev === path ? null : path));
    };

    const handleStartCreateProject = (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!projectsExpanded) {
            setProjectsExpanded(true);
            if (!projectsFetched) {
                setProjectsFetched(true);
                loadProjects();
            }
        }
        setIsCreatingProject(true);
        setNewProjectName('');
    };

    const handleCreateProjectSubmit = async () => {
        const trimmed = newProjectName.trim();
        setIsCreatingProject(false);
        if (!trimmed) return;
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.create_workspace_project === 'function') {
                const project = await api.create_workspace_project(trimmed) as WorkspaceProject;
                if (project?.path) {
                    setProjects((prev) => [...prev, project].sort((a, b) => a.name.localeCompare(b.name)));
                }
            }
        } catch (err) {
            console.error('Failed to create project:', err);
        }
    };

    const handleStartRenameProject = (e: React.MouseEvent, project: WorkspaceProject) => {
        e.stopPropagation();
        setRenamingProjectPath(project.path);
        setRenameProjectValue(project.name);
    };

    const handleRenameProjectSubmit = async (oldPath: string) => {
        const trimmed = renameProjectValue.trim();
        setRenamingProjectPath(null);
        if (!trimmed) return;
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.rename_workspace_project === 'function') {
                const renamed = await api.rename_workspace_project(oldPath, trimmed) as WorkspaceProject;
                if (renamed?.path) {
                    setProjects((prev) =>
                        prev.map((p) => (p.path === oldPath ? renamed : p)).sort((a, b) => a.name.localeCompare(b.name))
                    );
                    // Keep already-loaded chats and the active filter pointed at the new path.
                    conversations.forEach((c) => {
                        if (c.project_path === oldPath) updateConversation(c.id, { project_path: renamed.path });
                    });
                    setSelectedProjectPath((prev) => (prev === oldPath ? renamed.path : prev));
                }
            }
        } catch (err) {
            console.error('Failed to rename project:', err);
        }
    };

    const handleDeleteFromMenu = () => {
        if (!actionMenu) return;
        setDeleteModal({ isOpen: true, id: actionMenu.chatId, title: actionMenu.chatTitle });
        closeActionMenu();
    };

    const handlePullStart = (e: React.TouchEvent<HTMLDivElement>) => {
        const el = e.currentTarget;
        if (el.scrollTop > 0) return;
        isPulling.current = true;
        pullStartY.current = e.touches[0].clientY;
    };

    const handlePullMove = (e: React.TouchEvent<HTMLDivElement>) => {
        if (!isPulling.current) return;
        const delta = Math.max(0, e.touches[0].clientY - pullStartY.current);
        setPullY(Math.min(delta, PULL_THRESHOLD + 20));
        if (delta > PULL_THRESHOLD / 2) e.preventDefault();
    };

    const handlePullEnd = () => {
        if (!isPulling.current) return;
        isPulling.current = false;
        if (pullY >= PULL_THRESHOLD) {
            navigator.vibrate?.(10);
            onLoadMore?.();
        }
        setPullY(0);
    };

    const handleRecentsScroll = (e: React.UIEvent<HTMLDivElement>) => {
        const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
        if (!hasMoreConversations || isHistoryLoading || selectedProjectPath) return;
        if (scrollHeight - scrollTop - clientHeight < 80) {
            onLoadMore?.();
        }
    };

    const handleConfirmDelete = async (id: string) => {
        try {
            if (id === activeConversationId && onNewChat) {
                onNewChat();
            }

            if (window.pywebview?.api && typeof window.pywebview.api.delete_chat === 'function') {
                const success = await window.pywebview.api.delete_chat(id);
                if (success) {
                    removeConversation(id);
                }
            }
        } catch (err) {
            console.error('Failed to delete chat:', err);
        }
    };

    const renderRecentItem = (chat: Conversation) => {
        const isRenaming = renamingId === chat.id;
        const isMenuOpen = actionMenu?.chatId === chat.id;

        if (isRenaming) {
            return (
                <div key={chat.id} className="recent-item recent-item--renaming">
                    <input
                        className="recent-item__rename-input"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => handleRenameSubmit(chat.id)}
                        onKeyDown={(e) => handleRenameKeyDown(e, chat.id)}
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                    />
                </div>
            );
        }

        return (
            <div key={chat.id} className={`recent-item-wrapper ${isMenuOpen ? 'has-menu-open' : ''}`}>
                <button
                    className={`recent-item ${activeConversationId === chat.id ? 'is-active' : ''}`}
                    onClick={() => onChatSelect?.(chat.id)}
                >
                    {chat.is_pinned && (
                        <Pin size={12} className="recent-item__pin-icon" aria-label="Pinned" />
                    )}
                    <span className="recent-item__text" title={chat.title}>{chat.title}</span>
                    <div className="recent-item__actions">
                        <MoreHorizontal
                            size={14}
                            className={`recent-item__more ${isMenuOpen ? 'is-open' : ''}`}
                            onClick={(e) => handleMoreClick(e, chat)}
                        />
                    </div>
                </button>
                <AnimatePresence>
                    {isMenuOpen && (
                        <motion.div
                            ref={menuRef}
                            className="recent-item__menu"
                            initial={{ opacity: 0, y: -4, scale: 0.98 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: -4, scale: 0.98 }}
                            transition={{ duration: 0.12 }}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <button type="button" className="recent-item__menu-btn" onClick={() => handleRenameStart(chat)}>
                                <Pencil size={14} />
                                <span>Rename</span>
                            </button>
                            <button type="button" className="recent-item__menu-btn" onClick={() => handlePinToggle(chat)}>
                                <Pin size={14} />
                                <span>{chat.is_pinned ? 'Unpin' : 'Pin'}</span>
                            </button>
                            <button type="button" className="recent-item__menu-btn" onClick={() => handleMoveToProjectStart(chat)}>
                                <FolderInput size={14} />
                                <span>Move to Project</span>
                            </button>
                            <button type="button" className="recent-item__menu-btn recent-item__menu-btn--danger" onClick={handleDeleteFromMenu}>
                                <Trash2 size={14} />
                                <span>Delete</span>
                            </button>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        );
    };

    return (
        <motion.div
            className={`nav-sidebar ${isCollapsed ? 'is-collapsed' : ''}${isResizing ? ' is-resizing' : ''}`}
            style={!isCollapsed ? { width: navWidth } : undefined}
            initial={{ x: -260, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 200, damping: 25 }}
        >
            <div className="nav-sidebar__content">
                {/* Branding */}
                <div className="nav-sidebar__branding">
                    {!isCollapsed && (
                        <div className="branding-left">
                            <h1 className="nav-sidebar__title">CHAD</h1>
                        </div>
                    )}

                    <button
                        className="nav-sidebar__toggle-btn"
                        onClick={() => setIsCollapsed(!isCollapsed)}
                        title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                    >
                        <PanelLeft size={18} />
                    </button>
                </div>

                {/* Header Actions */}
                <div className="nav-sidebar__actions">
                    {/* Wrapped, not passed directly: `onNewChat` takes an optional `domainId`,
                        so `onClick={onNewChat}` hands React's MouseEvent in as the domain. That
                        made `setPendingDomain(domainId ?? null)` park a truthy event object
                        instead of clearing the category, so a plain "New chat" silently
                        inherited the previous chat's domain. */}
                    <button className="nav-sidebar__btn-new" onClick={() => onNewChat?.()} title="New chat">
                        <Plus size={18} />
                        {!isCollapsed && <span>New chat</span>}
                    </button>

                    {/* Collapsed: search stays reachable as an icon (Recent header is hidden). */}
                    {isCollapsed && (
                        <button
                            className="nav-sidebar__btn-secondary"
                            title="Search chats"
                            onClick={onSearchClick}
                            aria-label="Search chats"
                        >
                            <Search size={18} />
                        </button>
                    )}
                </div>

                {/* Main Navigation */}
                <nav className="nav-sidebar__nav">
                    <div className="nav-section">
                        <div className="nav-section__header" title="Projects" onClick={handleToggleProjects}>
                            <Folder size={16} />
                            {!isCollapsed && (
                                <>
                                    <span>Projects</span>
                                    <FolderInput
                                        size={13}
                                        className="nav-section__add"
                                        onClick={handleStartCreateProject}
                                        aria-label="New project"
                                    />
                                    <ChevronDown
                                        size={14}
                                        className={`nav-section__chevron ${projectsExpanded ? 'is-expanded' : ''}`}
                                    />
                                </>
                            )}
                        </div>

                        {!isCollapsed && projectsExpanded && (
                            <div className="projects-list">
                                {isCreatingProject && (
                                    <div className="recent-item recent-item--renaming">
                                        <input
                                            className="recent-item__rename-input"
                                            value={newProjectName}
                                            onChange={(e) => setNewProjectName(e.target.value)}
                                            onBlur={handleCreateProjectSubmit}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') { e.preventDefault(); handleCreateProjectSubmit(); }
                                                else if (e.key === 'Escape') setIsCreatingProject(false);
                                            }}
                                            placeholder="Project name..."
                                            autoFocus
                                            onClick={(e) => e.stopPropagation()}
                                        />
                                    </div>
                                )}
                                {projectsLoading ? (
                                    <NavSidebarSkeleton count={3} />
                                ) : projects.length === 0 && !isCreatingProject ? (
                                    <div className="recents-empty">No projects yet</div>
                                ) : (
                                    projects.map((project) =>
                                        renamingProjectPath === project.path ? (
                                            <div key={project.path} className="recent-item recent-item--renaming">
                                                <input
                                                    className="recent-item__rename-input"
                                                    value={renameProjectValue}
                                                    onChange={(e) => setRenameProjectValue(e.target.value)}
                                                    onBlur={() => handleRenameProjectSubmit(project.path)}
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter') { e.preventDefault(); handleRenameProjectSubmit(project.path); }
                                                        else if (e.key === 'Escape') setRenamingProjectPath(null);
                                                    }}
                                                    autoFocus
                                                    onClick={(e) => e.stopPropagation()}
                                                />
                                            </div>
                                        ) : (
                                            <button
                                                key={project.path}
                                                className={`recent-item ${selectedProjectPath === project.path ? 'is-active' : ''}`}
                                                onClick={() => handleSelectProject(project.path)}
                                                title={project.path}
                                            >
                                                <Folder size={13} />
                                                <span className="recent-item__text">{project.name}</span>
                                                <div className="recent-item__actions">
                                                    <Pencil
                                                        size={13}
                                                        className="recent-item__more"
                                                        onClick={(e) => handleStartRenameProject(e, project)}
                                                    />
                                                </div>
                                            </button>
                                        )
                                    )
                                )}
                            </div>
                        )}
                    </div>

                    <div className="nav-section">
                        <div className="nav-section__header" title="Artifacts" onClick={onArtifactsClick}>
                            <Shapes size={16} />
                            {!isCollapsed && (
                                <>
                                    <span>Artifacts</span>
                                </>
                            )}
                        </div>
                    </div>
                </nav>

                {/* Recents List */}
                {!isCollapsed && (
                    <div className="nav-sidebar__recents">
                        <div className="recents-label">
                            <span className="recents-label__text">
                                {selectedProjectPath
                                    ? projects.find((p) => p.path === selectedProjectPath)?.name ?? 'Project'
                                    : 'Recent'}
                            </span>
                            {selectedProjectPath && (
                                <X
                                    size={12}
                                    className="recents-label__clear"
                                    onClick={() => setSelectedProjectPath(null)}
                                    aria-label="Clear filters"
                                />
                            )}
                            <button
                                type="button"
                                className="recents-label__search"
                                title="Search chats"
                                aria-label="Search chats"
                                onClick={onSearchClick}
                            >
                                <Search size={14} />
                            </button>
                        </div>
                        <div
                            className="recents-list"
                            onScroll={handleRecentsScroll}
                            onTouchStart={handlePullStart}
                            onTouchMove={handlePullMove}
                            onTouchEnd={handlePullEnd}
                        >
                            {pullY > 0 && (
                                <div
                                    className="recents-pull-indicator"
                                    style={{ opacity: Math.min(pullY / PULL_THRESHOLD, 1), transform: `scaleX(${Math.min(pullY / PULL_THRESHOLD, 1)})` }}
                                />
                            )}
                            {/* `!isOffline` matters: history sync only runs once the Brain is
                                reachable, so while offline this spinner never resolves and the
                                sidebar sat on "Loading chats..." indefinitely. Fall through to
                                the offline block below, which offers a reconnect. */}
                            {isHistoryLoading && conversations.length === 0 && !isOffline ? (
                                <NavSidebarSkeleton count={6} />
                            ) : (
                                <>
                                    {visibleConversations.map(renderRecentItem)}
                                    {!selectedProjectPath && hasMoreConversations && (
                                        <button
                                            className="recents-load-more"
                                            onClick={onLoadMore}
                                            disabled={isHistoryLoading}
                                        >
                                            {isHistoryLoading ? (
                                                <Loader2 size={14} className="animate-spin" />
                                            ) : (
                                                <span>Load more</span>
                                            )}
                                        </button>
                                    )}
                                    {visibleConversations.length === 0 && !isHistoryLoading && (
                                        <div className="recents-empty">
                                            {selectedProjectPath ? (
                                                'No chats in this project yet'
                                            ) : isOffline ? (
                                                <div className="recents-empty__offline">
                                                    <span>Chad isn't reachable right now</span>
                                                    <button
                                                        className="recents-empty__reconnect-btn"
                                                        onClick={() => {
                                                            useAppStore.getState().setStatus('reconnecting');
                                                            useAppStore.getState().setStatusText('Trying to reconnect...');
                                                            void window.pywebview?.api?.reconnect_server?.();
                                                        }}
                                                    >
                                                        Try again
                                                    </button>
                                                </div>
                                            ) : (
                                                'No history yet'
                                            )}
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    </div>
                )}
            </div>

            <DeleteChatModal
                isOpen={deleteModal.isOpen}
                chatId={deleteModal.id}
                chatTitle={deleteModal.title}
                onClose={() => setDeleteModal({ ...deleteModal, isOpen: false })}
                onConfirm={handleConfirmDelete}
            />

            <MoveToProjectModal
                isOpen={!!moveProjectModal}
                chatTitle={moveProjectModal?.chatTitle ?? null}
                currentProjectPath={moveProjectModal?.projectPath}
                onClose={() => setMoveProjectModal(null)}
                onSelect={handleMoveToProjectSelect}
            />


            {!isCollapsed && (
                <div
                    className={`nav-sidebar__resize-handle${isResizing ? ' is-resizing' : ''}`}
                    onPointerDown={handleResizePointerDown}
                    onPointerMove={handleResizePointerMove}
                    onPointerUp={handleResizePointerUp}
                    onPointerCancel={handleResizePointerUp}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize chat sidebar"
                    aria-valuemin={MIN_NAV_WIDTH}
                    aria-valuemax={Math.floor(window.innerWidth * MAX_NAV_WIDTH_RATIO)}
                    aria-valuenow={navWidth}
                />
            )}
        </motion.div>
    );
}
