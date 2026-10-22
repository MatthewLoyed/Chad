import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Plus, Trash2, Zap, X, Pencil, Kanban, GripVertical } from 'lucide-react';
import WorkspacePane from './WorkspacePane';
import { ErrorState } from '../common/AsyncState';
import type { KanbanCard, ColumnId } from './kanban.types';
import { readCache, writeCache } from '../../utils/localCache';
import './BoardWorkspace.css';

const CACHE_KEY = 'kanban_cards';

interface BoardWorkspaceProps {
    /** Sends a card to Chad as a normal chat turn and returns to the chat workspace. */
    onDispatchToChad: (card: KanbanCard) => void;
}

const COLUMNS: { id: ColumnId; label: string; accent: string }[] = [
    { id: 'todo', label: 'Todo', accent: '96, 165, 250' },
    { id: 'in_progress', label: 'In Progress', accent: '250, 204, 21' },
    { id: 'needs_review', label: 'Needs Review', accent: '192, 132, 252' },
    { id: 'done', label: 'Done', accent: '52, 211, 153' },
];

/**
 * Personal kanban as a full workspace (was a modal). Same board logic with room
 * to breathe — full-height columns, richer card creation (title + description),
 * column-accented cards, and one-click dispatch to Chad.
 */
function BoardSkeleton() {
    return (
        <div className="board-scroll board-skeleton" aria-busy="true" aria-label="Loading board">
            <div className="board__columns">
                {COLUMNS.map((col, colIdx) => (
                    <div key={col.id} className="board-col" style={{ '--col-accent': col.accent } as React.CSSProperties}>
                        <div className="board-col__header">
                            <span className="board-col__dot" />
                            <span className="board-col__title">{col.label}</span>
                            <span className="board-col__count">2</span>
                        </div>
                        <div className="board-col__cards">
                            {[1, 2].map((cardIdx) => (
                                <div key={cardIdx} className="board-card board-skeleton-card">
                                    <div className="board-skeleton-line" style={{ width: (colIdx + cardIdx) % 2 === 0 ? '80%' : '65%', height: '14px', marginBottom: '10px' }} />
                                    <div className="board-skeleton-line" style={{ width: '45%', height: '11px' }} />
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

export default function BoardWorkspace({ onDispatchToChad }: BoardWorkspaceProps) {
    const cached = readCache<KanbanCard[]>(CACHE_KEY);
    const [cards, setCards] = useState<KanbanCard[]>(cached || []);
    const [loading, setLoading] = useState(!cached);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [addingTo, setAddingTo] = useState<ColumnId | null>(null);
    const [newTitle, setNewTitle] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [editingCardId, setEditingCardId] = useState<string | null>(null);
    const [editTitle, setEditTitle] = useState('');
    const [editDescription, setEditDescription] = useState('');
    const [draggingId, setDraggingId] = useState<string | null>(null);
    const [dragOverCol, setDragOverCol] = useState<ColumnId | null>(null);

    const fetchCards = useCallback(async () => {
        if (!window.pywebview?.api?.get_kanban_cards) {
            setLoadError(new Error('The board is unavailable on this client.'));
            setLoading(false);
            return;
        }
        setLoadError(null);
        try {
            const result = await window.pywebview.api.get_kanban_cards();
            const list = Array.isArray(result) ? (result as KanbanCard[]) : [];
            setCards(list);
            writeCache(CACHE_KEY, list);
        } catch (err) {
            // Surface it: an all-zero board that still offers "Add a card" is
            // indistinguishable from a board Chad simply couldn't load.
            console.error('[Kanban] Failed to fetch cards:', err);
            setLoadError(err);
        } finally {
            setLoading(false);
        }
    }, []);

    /** Update cards state and keep the local cache in sync with it. */
    const updateCards = (fn: (prev: KanbanCard[]) => KanbanCard[]) =>
        setCards((prev) => {
            const next = fn(prev);
            writeCache(CACHE_KEY, next);
            return next;
        });

    useEffect(() => {
        fetchCards();
    }, [fetchCards]);

    const startAdd = (columnId: ColumnId) => {
        setAddingTo(columnId);
        setNewTitle('');
        setNewDesc('');
    };

    const cancelAdd = () => {
        setAddingTo(null);
        setNewTitle('');
        setNewDesc('');
    };

    const handleAddCard = async (columnId: ColumnId) => {
        const title = newTitle.trim();
        if (!title) {
            cancelAdd();
            return;
        }
        const desc = newDesc.trim() || null;
        cancelAdd();
        const result = await window.pywebview?.api?.create_kanban_card?.(title, desc, columnId, 0);
        const card = (result as { success: boolean; card?: KanbanCard } | undefined)?.card;
        if (card) updateCards((prev) => [...prev, card]);
    };

    const handleDelete = async (cardId: string) => {
        updateCards((prev) => prev.filter((c) => c.id !== cardId));
        await window.pywebview?.api?.delete_kanban_card?.(cardId);
    };

    const startEdit = (card: KanbanCard) => {
        setEditingCardId(card.id);
        setEditTitle(card.title);
        setEditDescription(card.description || '');
    };

    const saveEdit = async () => {
        if (!editingCardId) return;
        const cardId = editingCardId;
        const title = editTitle.trim();
        updateCards((prev) =>
            prev.map((c) => (c.id === cardId ? { ...c, title, description: editDescription || null } : c))
        );
        setEditingCardId(null);
        await window.pywebview?.api?.update_kanban_card?.(cardId, title, editDescription || null);
    };

    const moveCard = async (cardId: string, columnId: ColumnId) => {
        updateCards((prev) => prev.map((c) => (c.id === cardId ? { ...c, column_id: columnId } : c)));
        await window.pywebview?.api?.move_kanban_card?.(cardId, columnId, 0);
    };

    const handleDrop = (columnId: ColumnId) => {
        if (draggingId) moveCard(draggingId, columnId);
        setDraggingId(null);
        setDragOverCol(null);
    };

    return (
        <WorkspacePane
            icon={Kanban}
            title="Board"
            subtitle="Your personal kanban — drag to organize, hit Go to hand a card to Chad."
            flush
        >
            {loading ? (
                <BoardSkeleton />
            ) : loadError ? (
                <ErrorState
                    title="Couldn't load your board"
                    error={loadError}
                    onRetry={() => { setLoading(true); fetchCards(); }}
                />
            ) : (
                <div className="board-scroll">
                    <div className="board__columns">
                        {COLUMNS.map((col) => {
                            const colCards = cards
                                .filter((c) => c.column_id === col.id)
                                .sort((a, b) => a.order_index - b.order_index);
                            const isOver = dragOverCol === col.id;
                            return (
                                <div
                                    key={col.id}
                                    className={`board-col ${isOver ? 'is-drop-target' : ''}`}
                                    onDragOver={(e) => {
                                        e.preventDefault();
                                        if (dragOverCol !== col.id) setDragOverCol(col.id);
                                    }}
                                    onDragLeave={(e) => {
                                        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                                            setDragOverCol((c) => (c === col.id ? null : c));
                                        }
                                    }}
                                    onDrop={() => handleDrop(col.id)}
                                    style={{ ['--col-accent' as string]: col.accent }}
                                >
                                    <div className="board-col__header">
                                        <span className="board-col__dot" />
                                        <span className="board-col__title">{col.label}</span>
                                        <span className="board-col__count">{colCards.length}</span>
                                        <button
                                            type="button"
                                            className="board-col__add"
                                            onClick={() => (addingTo === col.id ? cancelAdd() : startAdd(col.id))}
                                            title="Add card"
                                        >
                                            <Plus size={15} />
                                        </button>
                                    </div>

                                    <div className="board-col__cards">
                                        <AnimatePresence initial={false}>
                                            {addingTo === col.id && (
                                                <motion.form
                                                    key="add-form"
                                                    className="board-add"
                                                    initial={{ opacity: 0, height: 0 }}
                                                    animate={{ opacity: 1, height: 'auto' }}
                                                    exit={{ opacity: 0, height: 0 }}
                                                    onSubmit={(e) => {
                                                        e.preventDefault();
                                                        handleAddCard(col.id);
                                                    }}
                                                >
                                                    <input
                                                        autoFocus
                                                        value={newTitle}
                                                        onChange={(e) => setNewTitle(e.target.value)}
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Escape') cancelAdd();
                                                        }}
                                                        placeholder="Task title"
                                                        className="board-add__title"
                                                    />
                                                    <textarea
                                                        value={newDesc}
                                                        onChange={(e) => setNewDesc(e.target.value)}
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Escape') cancelAdd();
                                                            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                                                                e.preventDefault();
                                                                handleAddCard(col.id);
                                                            }
                                                        }}
                                                        placeholder="Description (optional)"
                                                        className="board-add__desc"
                                                        rows={2}
                                                    />
                                                    <div className="board-add__actions">
                                                        <button type="submit" className="board-add__save">
                                                            Add card
                                                        </button>
                                                        <button
                                                            type="button"
                                                            className="board-add__cancel"
                                                            onClick={cancelAdd}
                                                        >
                                                            <X size={14} />
                                                        </button>
                                                    </div>
                                                </motion.form>
                                            )}
                                        </AnimatePresence>

                                        {colCards.map((card) => (
                                            <motion.div
                                                key={card.id}
                                                className={`board-card ${draggingId === card.id ? 'is-dragging' : ''}`}
                                                draggable={editingCardId !== card.id}
                                                onDragStart={() => setDraggingId(card.id)}
                                                onDragEnd={() => {
                                                    setDraggingId(null);
                                                    setDragOverCol(null);
                                                }}
                                                initial={{ opacity: 0, y: 6 }}
                                                animate={{ opacity: 1, y: 0 }}
                                                layout
                                            >
                                                {editingCardId === card.id ? (
                                                    <div className="board-card__edit">
                                                        <input
                                                            autoFocus
                                                            value={editTitle}
                                                            onChange={(e) => setEditTitle(e.target.value)}
                                                            className="board-add__title"
                                                        />
                                                        <textarea
                                                            value={editDescription}
                                                            onChange={(e) => setEditDescription(e.target.value)}
                                                            placeholder="Description (optional)"
                                                            className="board-add__desc"
                                                            rows={3}
                                                        />
                                                        <div className="board-add__actions">
                                                            <button
                                                                type="button"
                                                                onClick={saveEdit}
                                                                className="board-add__save"
                                                            >
                                                                Save
                                                            </button>
                                                            <button
                                                                type="button"
                                                                onClick={() => setEditingCardId(null)}
                                                                className="board-add__cancel"
                                                            >
                                                                <X size={14} />
                                                            </button>
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <>
                                                        <div className="board-card__grip">
                                                            <GripVertical size={13} />
                                                        </div>
                                                        <div className="board-card__main">
                                                            <div className="board-card__title-row">
                                                                <span className="board-card__title">
                                                                    {card.title}
                                                                </span>
                                                                <div className="board-card__tools">
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => startEdit(card)}
                                                                        title="Edit"
                                                                    >
                                                                        <Pencil size={12} />
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        className="board-card__del"
                                                                        onClick={() => handleDelete(card.id)}
                                                                        title="Delete"
                                                                    >
                                                                        <Trash2 size={12} />
                                                                    </button>
                                                                </div>
                                                            </div>
                                                            {card.description && (
                                                                <p className="board-card__desc">
                                                                    {card.description}
                                                                </p>
                                                            )}
                                                            <button
                                                                type="button"
                                                                className="board-card__go"
                                                                onClick={() => onDispatchToChad(card)}
                                                                title="Have Chad do this"
                                                            >
                                                                <Zap size={12} />
                                                                <span>Hand to Chad</span>
                                                            </button>
                                                        </div>
                                                    </>
                                                )}
                                            </motion.div>
                                        ))}

                                        {colCards.length === 0 && addingTo !== col.id && (
                                            <button
                                                type="button"
                                                className="board-col__empty"
                                                onClick={() => startAdd(col.id)}
                                            >
                                                <Plus size={14} /> Add a card
                                            </button>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </WorkspacePane>
    );
}
