import { useEffect, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    X,
    Shapes,
    Download,
    Clock,
    HardDrive,
    Search,
    RefreshCw,
    Eye,
} from 'lucide-react';
import { toHttpBase } from '../../../transport/transportConfig';
import { useAppStore } from '../../../store';
import { getFileTypeInfo } from '../../data-display/artifactTypes';
import { downloadArtifact } from '../../data-display/artifactFiles';
import { openArtifactPreview } from '../../data-display/openArtifactPreview';
import './ArtifactsModal.css';

interface Artifact {
    token: string;
    name: string;
    size: number;
    created_at: number;
    path: string;
}

interface ArtifactsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

function ArtifactsModalSkeleton() {
    return (
        <div className="artifacts-list artifacts-skeleton-list" aria-busy="true" aria-label="Loading artifacts">
            {[1, 2, 3, 4].map(idx => (
                <div key={idx} className="artifact-item artifact-skeleton-item">
                    <div className="artifact-skeleton-icon" />
                    <div className="artifact-item__info">
                        <div className="name-row">
                            <div className="artifact-skeleton-line artifact-skeleton-line--title" style={{ width: idx % 2 === 0 ? '160px' : '220px' }} />
                            <div className="artifact-skeleton-line artifact-skeleton-line--badge" style={{ width: '60px' }} />
                        </div>
                        <div className="meta-row">
                            <div className="artifact-skeleton-line artifact-skeleton-line--meta" style={{ width: '80px' }} />
                            <div className="artifact-skeleton-line artifact-skeleton-line--meta" style={{ width: '110px' }} />
                        </div>
                    </div>
                </div>
            ))}
        </div>
    );
}

export default function ArtifactsModal({ isOpen, onClose }: ArtifactsModalProps) {
    const [artifacts, setArtifacts] = useState<Artifact[]>([]);
    const [loading, setLoading] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const serverUrl = useAppStore(state => state.serverUrl);

    const fetchArtifacts = useCallback(async () => {
        if (!serverUrl) return;
        setLoading(true);
        try {
            const base = toHttpBase(serverUrl);
            const response = await fetch(`${base}/artifacts`);
            const data = await response.json();
            if (data.artifacts) {
                setArtifacts(data.artifacts);
            }
        } catch (err) {
            console.error('Failed to fetch artifacts:', err);
        } finally {
            setLoading(false);
        }
    }, [serverUrl]);

    useEffect(() => {
        if (isOpen) {
            fetchArtifacts();
        }
    }, [isOpen, fetchArtifacts]);

    const handleDownload = (e: React.MouseEvent, art: Artifact) => {
        e.stopPropagation();
        downloadArtifact(art.token, art.name, serverUrl);
    };

    const handlePreview = (art: Artifact) => {
        const info = getFileTypeInfo(art.name);
        if (info.canPreview) {
            openArtifactPreview({
                token: art.token,
                filename: art.name,
                serverUrl,
            });
            onClose();
        }
    };

    const formatSize = (bytes: number) => {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    };

    const formatDate = (timestamp: number) => {
        return new Date(timestamp * 1000).toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    };

    const filteredArtifacts = artifacts.filter(a =>
        a.name.toLowerCase().includes(searchQuery.toLowerCase())
    );

    if (!isOpen) return null;

    return (
        <AnimatePresence>
                <motion.div
                    className="modal-overlay"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    onClick={onClose}
                >
                    <motion.div
                        className="artifacts-modal"
                        initial={{ scale: 0.9, opacity: 0, y: 20 }}
                        animate={{ scale: 1, opacity: 1, y: 0 }}
                        exit={{ scale: 0.9, opacity: 0, y: 20 }}
                        onClick={e => e.stopPropagation()}
                    >
                        <div className="artifacts-modal__header">
                            <div className="header-title">
                                <Shapes size={20} className="header-icon" />
                                <h2>Unified Artifacts</h2>
                                <span className="count-badge">{artifacts.length}</span>
                            </div>

                            <div className="header-actions">
                                <div className="search-box">
                                    <Search size={16} />
                                    <input
                                        type="text"
                                        placeholder="Filter files..."
                                        value={searchQuery}
                                        onChange={e => setSearchQuery(e.target.value)}
                                    />
                                </div>

                                <button
                                    className={`action-btn ${loading ? 'spinning' : ''}`}
                                    onClick={fetchArtifacts}
                                    disabled={loading}
                                >
                                    <RefreshCw size={18} />
                                </button>

                                <button className="close-btn" onClick={onClose}>
                                    <X size={20} />
                                </button>
                            </div>
                        </div>

                        <div className="artifacts-modal__content">
                            {loading && artifacts.length === 0 ? (
                                <ArtifactsModalSkeleton />
                            ) : filteredArtifacts.length > 0 ? (
                                <div className="artifacts-list">
                                    {filteredArtifacts.map(art => {
                                        const info = getFileTypeInfo(art.name);
                                        return (
                                            <div
                                                key={art.token}
                                                className={`artifact-item ${info.canPreview ? 'artifact-item--previewable' : ''}`}
                                                style={
                                                    {
                                                        borderLeft: `3px solid ${info.themeColor}`,
                                                        '--theme-color': info.themeColor,
                                                    } as React.CSSProperties
                                                }
                                                onClick={() => handlePreview(art)}
                                            >
                                                <div
                                                    className="artifact-item__icon"
                                                    style={{
                                                        color: info.themeColor,
                                                        background: `${info.themeColor}15`,
                                                    }}
                                                >
                                                    {info.icon}
                                                </div>
                                                <div className="artifact-item__info">
                                                    <div className="name-row">
                                                        <span className="file-name">{art.name}</span>
                                                        <span className="token-label">{art.token}</span>
                                                    </div>
                                                    <div className="meta-row">
                                                        <div className="meta-item">
                                                            <HardDrive size={12} />
                                                            <span>{formatSize(art.size)}</span>
                                                        </div>
                                                        <div className="meta-item">
                                                            <Clock size={12} />
                                                            <span>{formatDate(art.created_at)}</span>
                                                        </div>
                                                        <div className="meta-item">
                                                            <span>{info.label}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="artifact-item__actions">
                                                    {info.canPreview && (
                                                        <button
                                                            type="button"
                                                            className="preview-btn"
                                                            style={{
                                                                background: `${info.themeColor}20`,
                                                                borderColor: `${info.themeColor}30`,
                                                                color: info.themeColor,
                                                            }}
                                                            onClick={e => {
                                                                e.stopPropagation();
                                                                handlePreview(art);
                                                            }}
                                                            title="Preview in app"
                                                        >
                                                            <Eye size={18} />
                                                        </button>
                                                    )}
                                                    <button
                                                        type="button"
                                                        className="download-btn"
                                                        style={{
                                                            background: `${info.themeColor}20`,
                                                            borderColor: `${info.themeColor}30`,
                                                            color: info.themeColor,
                                                        }}
                                                        onClick={e => handleDownload(e, art)}
                                                        title="Save to system"
                                                    >
                                                        <Download size={18} />
                                                    </button>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="empty-state">
                                    <Shapes size={40} />
                                    <p>
                                        {searchQuery
                                            ? 'No matching files found'
                                            : 'No artifacts generated yet'}
                                    </p>
                                </div>
                            )}
                        </div>

                        <div className="artifacts-modal__footer">
                            <div className="storage-info">
                                <HardDrive size={14} />
                                <span>
                                    Storage Root: <code>/artifacts</code>
                                </span>
                            </div>
                            <p className="footer-hint">
                                All files are permanently stored in your project directory.
                            </p>
                        </div>
                    </motion.div>
                </motion.div>
        </AnimatePresence>
    );
}
