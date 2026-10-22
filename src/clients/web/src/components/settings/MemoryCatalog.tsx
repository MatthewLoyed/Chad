import { useCallback, useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, RefreshCw, Trash2 } from 'lucide-react';
import '../modals/memory/MemoryModal.css';
import './MemoryCatalog.css';

export interface MemoryItem {
    id: string;
    content: string;
    type: string;
    timestamp?: string;
    source?: string;
}

export interface MemorySection {
    id: string;
    label: string;
    description: string;
    total_count: number;
    preview: MemoryItem[];
    deletable: boolean;
    source: string;
    category?: string;
}

interface SectionRuntime {
    items: MemoryItem[];
    hasMore: boolean;
    loadingMore: boolean;
}

interface MemoryCatalogProps {
    active: boolean;
    searchQuery: string;
}

const DEFAULT_EXPANDED = new Set(['knowledge_graph', 'preference', 'relationships', 'approved_lessons']);

function formatTimestamp(ts?: string) {
    if (!ts) return null;
    try {
        return new Date(ts).toLocaleString();
    } catch {
        return null;
    }
}

export default function MemoryCatalog({ active, searchQuery }: MemoryCatalogProps) {
    const [sections, setSections] = useState<MemorySection[]>([]);
    const [sectionState, setSectionState] = useState<Record<string, SectionRuntime>>({});
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [searchResults, setSearchResults] = useState<MemoryItem[] | null>(null);
    const [searchLoading, setSearchLoading] = useState(false);

    const loadCatalog = useCallback(async () => {
        const api = window.pywebview?.api;
        if (!api?.get_memory_catalog) {
            setError('Brain bridge not available — reconnect or restart Chad.');
            setLoading(false);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const result = await api.get_memory_catalog() as { sections?: MemorySection[] } | null;
            if (result === null) {
                setError('Memory system is still initializing. Try again in a moment.');
                setSections([]);
                return;
            }
            const nextSections = Array.isArray(result?.sections) ? result.sections : [];
            setSections(nextSections);

            const runtime: Record<string, SectionRuntime> = {};
            const open = new Set<string>();
            for (const section of nextSections) {
                runtime[section.id] = {
                    items: section.preview ?? [],
                    hasMore: section.total_count > (section.preview?.length ?? 0),
                    loadingMore: false,
                };
                if (section.total_count > 0 && DEFAULT_EXPANDED.has(section.id)) {
                    open.add(section.id);
                }
            }
            setSectionState(runtime);
            setExpanded(open);
        } catch (err) {
            console.error('Failed to load memory catalog:', err);
            setError('Could not load memories from the Brain. Try again.');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (!active) return;
        void loadCatalog();
    }, [active, loadCatalog]);

    useEffect(() => {
        const q = searchQuery.trim();
        if (q.length < 2) {
            setSearchResults(null);
            return;
        }
        const api = window.pywebview?.api;
        if (!api?.search_memories) return;

        const timer = window.setTimeout(async () => {
            setSearchLoading(true);
            try {
                const rows = await api.search_memories(q) as MemoryItem[];
                setSearchResults(Array.isArray(rows) ? rows : []);
            } catch {
                setSearchResults([]);
            } finally {
                setSearchLoading(false);
            }
        }, 250);

        return () => window.clearTimeout(timer);
    }, [searchQuery]);

    const toggleSection = (sectionId: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(sectionId)) next.delete(sectionId);
            else next.add(sectionId);
            return next;
        });
    };

    const loadMore = async (section: MemorySection) => {
        const api = window.pywebview?.api;
        if (!api?.get_memory_section) return;

        const current = sectionState[section.id];
        if (!current || current.loadingMore || !current.hasMore) return;

        setSectionState((prev) => ({
            ...prev,
            [section.id]: { ...prev[section.id], loadingMore: true },
        }));

        try {
            const page = await api.get_memory_section(
                section.id,
                current.items.length,
                25,
            ) as { items?: MemoryItem[]; has_more?: boolean };
            const newItems = Array.isArray(page?.items) ? page.items : [];
            setSectionState((prev) => ({
                ...prev,
                [section.id]: {
                    items: [...prev[section.id].items, ...newItems],
                    hasMore: !!page?.has_more,
                    loadingMore: false,
                },
            }));
        } catch (err) {
            console.error('Failed to load more memories:', err);
            setSectionState((prev) => ({
                ...prev,
                [section.id]: { ...prev[section.id], loadingMore: false },
            }));
        }
    };

    const handleDelete = async (sectionId: string, itemId: string, deletable: boolean) => {
        if (!deletable) return;
        const api = window.pywebview?.api;
        if (!api?.delete_memory) return;
        await api.delete_memory(itemId);
        setSectionState((prev) => {
            const block = prev[sectionId];
            if (!block) return prev;
            return {
                ...prev,
                [sectionId]: {
                    ...block,
                    items: block.items.filter((m) => m.id !== itemId),
                },
            };
        });
        setSections((prev) =>
            prev.map((s) =>
                s.id === sectionId
                    ? { ...s, total_count: Math.max(0, s.total_count - 1) }
                    : s,
            ),
        );
    };

    const visibleSections = useMemo(() => {
        const q = searchQuery.trim().toLowerCase();
        if (!q || q.length >= 2) return sections;
        return sections
            .map((section) => {
                const items = (sectionState[section.id]?.items ?? section.preview).filter((m) =>
                    m.content.toLowerCase().includes(q) ||
                    m.type.toLowerCase().includes(q),
                );
                return { ...section, filteredItems: items };
            })
            .filter((s) => (s as MemorySection & { filteredItems: MemoryItem[] }).filteredItems.length > 0);
    }, [sections, sectionState, searchQuery]);

    const isSearchMode = searchQuery.trim().length >= 2;

    if (loading) {
        return (
            <div className="memory-catalog">
                <div className="memory-catalog__toolbar">
                    <p className="settings-hint">
                        Organized by type — user facts and relationships come from your knowledge graph;
                        activity and session logs live in cloud memory.
                    </p>
                </div>
                <div className="memory-catalog-skeleton" aria-busy="true" aria-label="Loading memory">
                    {[1, 2, 3].map(idx => (
                        <div key={idx} className="memory-section memory-skeleton-section">
                            <div className="memory-section__head">
                                <div className="memory-section__meta">
                                    <div className="memory-skeleton-line memory-skeleton-line--title" style={{ width: idx === 1 ? '140px' : idx === 2 ? '190px' : '160px' }} />
                                    <div className="memory-skeleton-line memory-skeleton-line--desc" style={{ width: idx === 1 ? '240px' : '200px' }} />
                                </div>
                                <div className="memory-skeleton-badge" />
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        );
    }
    if (error) {
        return (
            <div className="memory-catalog__error">
                <p className="memory-empty">{error}</p>
                <button type="button" className="memory-catalog__retry" onClick={() => void loadCatalog()}>
                    <RefreshCw size={14} /> Retry
                </button>
            </div>
        );
    }

    return (
        <div className="memory-catalog">
            <div className="memory-catalog__toolbar">
                <p className="settings-hint">
                    Organized by type — user facts and relationships come from your knowledge graph;
                    activity and session logs live in cloud memory.
                </p>
                <button
                    type="button"
                    className="settings-memory-refresh"
                    onClick={() => void loadCatalog()}
                    disabled={loading}
                >
                    <RefreshCw size={14} className={loading ? 'is-spinning' : ''} />
                    Refresh
                </button>
            </div>

            {isSearchMode ? (
                <div className="memory-catalog__search-results">
                    <div className="memory-section__head">
                        <span className="memory-section__title">Search results</span>
                        <span className="memory-section__count">
                            {searchLoading ? '…' : (searchResults?.length ?? 0)}
                        </span>
                    </div>
                    {searchLoading ? (
                        <div className="memory-empty">Searching…</div>
                    ) : searchResults && searchResults.length > 0 ? (
                        <MemoryItemList
                            items={searchResults}
                            deletable
                            onDelete={(id) => void handleDelete('_search', id, true)}
                        />
                    ) : (
                        <div className="memory-empty">No matches found</div>
                    )}
                </div>
            ) : (
                <div className="memory-catalog__sections">
                    {visibleSections.length === 0 ? (
                        <div className="memory-empty">No memories stored yet</div>
                    ) : (
                        visibleSections.map((section) => {
                            const runtime = sectionState[section.id];
                            const items =
                                (section as MemorySection & { filteredItems?: MemoryItem[] }).filteredItems ??
                                runtime?.items ??
                                section.preview;
                            const isOpen = expanded.has(section.id);

                            return (
                                <section key={section.id} className="memory-section">
                                    <button
                                        type="button"
                                        className={`memory-section__head ${isOpen ? 'is-open' : ''}`}
                                        onClick={() => toggleSection(section.id)}
                                        aria-expanded={isOpen}
                                    >
                                        <div className="memory-section__meta">
                                            <span className="memory-section__title">{section.label}</span>
                                            {section.description && (
                                                <span className="memory-section__desc">{section.description}</span>
                                            )}
                                        </div>
                                        <span className="memory-section__count">{section.total_count}</span>
                                        <ChevronDown size={16} className="memory-section__chevron" />
                                    </button>

                                    <AnimatePresence initial={false}>
                                        {isOpen && (
                                            <motion.div
                                                className="memory-section__body"
                                                initial={{ height: 0, opacity: 0 }}
                                                animate={{ height: 'auto', opacity: 1 }}
                                                exit={{ height: 0, opacity: 0 }}
                                                transition={{ duration: 0.18 }}
                                            >
                                                {items.length === 0 ? (
                                                    <div className="memory-empty memory-empty--compact">
                                                        Nothing in this section yet
                                                    </div>
                                                ) : (
                                                    <MemoryItemList
                                                        items={items}
                                                        deletable={section.deletable}
                                                        onDelete={(id) =>
                                                            void handleDelete(section.id, id, section.deletable)
                                                        }
                                                    />
                                                )}
                                                {runtime?.hasMore && (
                                                    <button
                                                        type="button"
                                                        className="memory-section__more"
                                                        onClick={() => void loadMore(section)}
                                                        disabled={runtime.loadingMore}
                                                    >
                                                        {runtime.loadingMore ? 'Loading…' : 'Show more'}
                                                    </button>
                                                )}
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </section>
                            );
                        })
                    )}
                </div>
            )}
        </div>
    );
}

function MemoryItemList({
    items,
    deletable,
    onDelete,
}: {
    items: MemoryItem[];
    deletable: boolean;
    onDelete: (id: string) => void;
}) {
    return (
        <div className="memory-list memory-list--section">
            {items.map((memory, index) => (
                <motion.div
                    key={`${memory.id}-${index}`}
                    className="memory-item"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: Math.min(index * 0.03, 0.2) }}
                >
                    <div className="memory-item__content">
                        <span className="memory-item__type">{memory.type}</span>
                        <p className="memory-item__text">{memory.content}</p>
                        {formatTimestamp(memory.timestamp) && (
                            <span className="memory-item__time">{formatTimestamp(memory.timestamp)}</span>
                        )}
                    </div>
                    {deletable && (
                        <motion.button
                            type="button"
                            className="memory-item__delete"
                            onClick={() => onDelete(memory.id)}
                            whileHover={{ scale: 1.08 }}
                            whileTap={{ scale: 0.92 }}
                            title="Delete memory"
                        >
                            <Trash2 size={16} />
                        </motion.button>
                    )}
                </motion.div>
            ))}
        </div>
    );
}
