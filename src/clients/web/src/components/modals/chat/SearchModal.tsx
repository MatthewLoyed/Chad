import { useState, useEffect, useCallback } from 'react';
import { Search, MessageSquare, Clock, ArrowRight, User } from 'lucide-react';
import { ChadAvatar } from '../../common/chad-avatar';
import Modal from '../base/Modal';
import './SearchModal.css';

interface SearchModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSelectChat: (id: string) => void;
}

interface SearchMessageResult {
    id: string;
    conversation_id: string;
    role: 'user' | 'assistant';
    content: unknown;
    conversation_title: string;
    created_at: string;
    /** Ranked match window from FTS/backend when present. */
    snippet?: string;
    rank?: number | null;
}

interface SearchConversationResult {
    id: string;
    title: string;
    updated_at: string;
}

interface SearchResult {
    messages: SearchMessageResult[];
    conversations: SearchConversationResult[];
}

function SearchModalSkeleton() {
    return (
        <div className="search-skeleton-list" aria-busy="true" aria-label="Searching chats">
            {[1, 2, 3, 4].map(idx => (
                <div key={idx} className="search-skeleton-item">
                    <div className="search-skeleton-icon" />
                    <div className="search-skeleton-info">
                        <div className="search-skeleton-line search-skeleton-line--title" style={{ width: idx % 2 === 0 ? '55%' : '75%' }} />
                        <div className="search-skeleton-line search-skeleton-line--meta" style={{ width: idx % 2 === 0 ? '30%' : '40%' }} />
                    </div>
                </div>
            ))}
        </div>
    );
}

export default function SearchModal({ isOpen, onClose, onSelectChat }: SearchModalProps) {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<SearchResult>({ messages: [], conversations: [] });
    const [loading, setLoading] = useState(false);

    const performSearch = useCallback(async (searchQuery: string) => {
        if (!searchQuery.trim()) {
            setResults({ messages: [], conversations: [] });
            return;
        }

        setLoading(true);
        try {
            if (window.pywebview?.api?.search_chats) {
                const res = await window.pywebview.api.search_chats(searchQuery) as SearchResult;
                setResults({
                    messages: res?.messages || [],
                    conversations: res?.conversations || []
                });
            }
        } catch (error) {
            console.error('Search failed:', error);
            setResults({ messages: [], conversations: [] });
        } finally {
            setLoading(false);
        }
    }, []);

    // Debounce search
    useEffect(() => {
        if (!isOpen) return;
        
        const timer = setTimeout(() => {
            performSearch(query);
        }, 300);
        return () => clearTimeout(timer);
    }, [query, performSearch, isOpen]);

    // Clear results when modal opens
    useEffect(() => {
        if (!isOpen) {
            setQuery('');
            setResults({ messages: [], conversations: [] });
        }
    }, [isOpen]);

    const handleSelect = (id: string) => {
        onSelectChat(id);
        onClose();
    };

    const getSnippet = (msg: SearchMessageResult) => {
        if (typeof msg.snippet === 'string' && msg.snippet.trim()) {
            return msg.snippet;
        }
        const content = msg.content;
        if (typeof content === 'string') {
            return content;
        }
        if (Array.isArray(content)) {
            return content
                .filter((b): b is { type: string; text: string } => 
                    typeof b === 'object' && b !== null && 'type' in b && b.type === 'text' && 'text' in b && typeof (b as Record<string, unknown>).text === 'string'
                )
                .map(b => b.text)
                .join(' ');
        }
        return 'Complex content';
    };

    const highlightMatch = (text: string, match: string) => {
        if (!match.trim() || typeof text !== 'string') return text;
        const parts = text.split(new RegExp(`(${match})`, 'gi'));
        return (
            <span>
                {parts.map((part, i) => 
                    part.toLowerCase() === match.toLowerCase() 
                        ? <mark key={i} className="search-highlight">{part}</mark> 
                        : part
                )}
            </span>
        );
    };

    const truncateSnippet = (text: string, maxLength: number = 150) => {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength) + '...';
    };

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="Search History">
            <div className="search-modal__container">
                <div className="search-input-wrapper">
                    <Search className="search-icon" size={20} />
                    <input
                        autoFocus
                        type="text"
                        className="search-input"
                        placeholder="Search conversations and messages..."
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                    {loading && (
                        <div className="search-spinner">
                            <div className="spinner-inner"></div>
                        </div>
                    )}
                </div>

                <div className="search-results">
                    {!query && (
                        <div className="search-empty-state">
                            <Search className="empty-icon" size={48} />
                            <p>Type to search through your chat history</p>
                        </div>
                    )}

                    {query && loading && (
                        <SearchModalSkeleton />
                    )}

                    {query && !loading && results.conversations.length === 0 && results.messages.length === 0 && (
                        <div className="search-empty-state">
                            <p>No results found for "{query}"</p>
                        </div>
                    )}

                    {(results.conversations.length > 0 || results.messages.length > 0) && (
                        <div className="results-scroll-area">
                            {results.conversations.length > 0 && (
                                <div className="result-section">
                                    <h3 className="section-title">Conversations</h3>
                                    {results.conversations.map((conv) => (
                                        <div 
                                            key={conv.id} 
                                            className="result-item conversation-result"
                                            onClick={() => handleSelect(conv.id)}
                                        >
                                            <MessageSquare size={16} className="result-icon" />
                                            <div className="result-info">
                                                <span className="result-title">
                                                    {highlightMatch(conv.title, query)}
                                                </span>
                                                <span className="result-meta">
                                                    <Clock size={12} />
                                                    {new Date(conv.updated_at).toLocaleDateString()}
                                                </span>
                                            </div>
                                            <ArrowRight size={16} className="arrow-icon" />
                                        </div>
                                    ))}
                                </div>
                            )}

                            {results.messages.length > 0 && (
                                <div className="result-section">
                                    <h3 className="section-title">Messages</h3>
                                    {results.messages.map((msg) => (
                                        <div 
                                            key={msg.id} 
                                            className="result-item message-result"
                                            onClick={() => handleSelect(msg.conversation_id)}
                                        >
                                            {msg.role === 'user' ? (
                                                <User size={16} className="result-icon" />
                                            ) : (
                                                <div className="result-icon"><ChadAvatar size={16} /></div>
                                            )}
                                            <div className="result-info">
                                                <p className="result-snippet">
                                                    {highlightMatch(truncateSnippet(getSnippet(msg)), query)}
                                                </p>
                                                <div className="result-meta">
                                                    <span className="source-chat">in {msg.conversation_title}</span>
                                                    <span className="dot">•</span>
                                                    <span className="date">{new Date(msg.created_at).toLocaleDateString()}</span>
                                                </div>
                                            </div>
                                            <ArrowRight size={16} className="arrow-icon" />
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </Modal>
    );
}
