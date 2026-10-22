import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Folder, Loader2 } from 'lucide-react';
import './MoveToProjectModal.css';

export interface WorkspaceProject {
    path: string;
    name: string;
}

interface MoveToProjectModalProps {
    isOpen: boolean;
    chatTitle: string | null;
    currentProjectPath?: string | null;
    onClose: () => void;
    onSelect: (projectPath: string | null) => void;
}

export default function MoveToProjectModal({
    isOpen,
    chatTitle,
    currentProjectPath,
    onClose,
    onSelect,
}: MoveToProjectModalProps) {
    const [projects, setProjects] = useState<WorkspaceProject[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!isOpen) return;

        const loadProjects = async () => {
            setLoading(true);
            setError(null);
            try {
                const api = window.pywebview?.api;
                if (api && typeof api.get_workspace_projects === 'function') {
                    const res = await api.get_workspace_projects() as WorkspaceProject[];
                    setProjects(Array.isArray(res) ? res : []);
                } else {
                    setProjects([]);
                    setError('Project list is unavailable.');
                }
            } catch (err) {
                console.error('Failed to load workspace projects:', err);
                setError('Could not load projects.');
            } finally {
                setLoading(false);
            }
        };

        loadProjects();
    }, [isOpen]);

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <div className="move-project-overlay" onClick={onClose}>
                <motion.div
                    className="move-project-content"
                    initial={{ opacity: 0, scale: 0.95, y: 8 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.95, y: 8 }}
                    transition={{ duration: 0.18, ease: 'easeOut' }}
                    onClick={(e) => e.stopPropagation()}
                >
                    <h2 className="move-project-title">Move to project</h2>
                    <p className="move-project-subtitle">
                        Choose a workspace for <strong>{chatTitle || 'this chat'}</strong>.
                    </p>

                    {loading && (
                        <div className="move-project-loading">
                            <Loader2 size={18} className="animate-spin" />
                            <span>Loading projects...</span>
                        </div>
                    )}

                    {!loading && error && <p className="move-project-error">{error}</p>}

                    {!loading && !error && (
                        <div className="move-project-list">
                            <button
                                type="button"
                                className={`move-project-option ${!currentProjectPath ? 'is-selected' : ''}`}
                                onClick={() => onSelect(null)}
                            >
                                <span>No project</span>
                            </button>
                            {projects.map((project) => (
                                <button
                                    key={project.path}
                                    type="button"
                                    className={`move-project-option ${currentProjectPath === project.path ? 'is-selected' : ''}`}
                                    onClick={() => onSelect(project.path)}
                                >
                                    <Folder size={14} />
                                    <span className="move-project-option__name">{project.name}</span>
                                    <span className="move-project-option__path">{project.path}</span>
                                </button>
                            ))}
                            {projects.length === 0 && (
                                <p className="move-project-empty">No projects found under your coding roots.</p>
                            )}
                        </div>
                    )}

                    <div className="move-project-footer">
                        <button type="button" className="move-project-btn move-project-btn--cancel" onClick={onClose}>
                            Cancel
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
}
