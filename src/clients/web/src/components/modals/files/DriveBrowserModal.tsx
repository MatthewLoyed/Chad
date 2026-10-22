import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Search, File, FileText, Folder, Cloud, Download, Loader2, ExternalLink } from 'lucide-react';
import { SiGoogledocs, SiGooglesheets, SiGoogledrive } from 'react-icons/si';
import './DriveBrowserModal.css';

interface DriveFile {
    id: string;
    name: string;
    mimeType: string;
    modifiedTime: string;
    size?: string;
    iconLink?: string;
    webViewLink?: string;
}

interface DriveBrowserModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSelect: (file: { name: string, content: string }) => void;
}

export default function DriveBrowserModal({ isOpen, onClose, onSelect }: DriveBrowserModalProps) {
    const [files, setFiles] = useState<DriveFile[]>([]);
    const [loading, setLoading] = useState(false);
    const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [selectingId, setSelectingId] = useState<string | null>(null);
    const [isAuthorizing, setIsAuthorizing] = useState(false);

    const fetchFiles = async (query = '') => {
        setLoading(true);
        setError(null);
        try {
            const api = window.pywebview?.api;
            if (api) {
                // 1. Check Auth first
                if (typeof api.check_google_auth === 'function') {
                    const authResult = await api.check_google_auth() as { authenticated: boolean };
                    setIsAuthenticated(authResult.authenticated);
                    
                    if (!authResult.authenticated) {
                        setLoading(false);
                        return;
                    }
                }

                // 2. Fetch files if authed
                if (typeof api.list_drive_files === 'function') {
                    const result = await api.list_drive_files(query) as { success: boolean; files?: DriveFile[]; error?: string };
                    if (result.success) {
                        setFiles(result.files || []);
                    } else {
                        setError(result.error || 'Failed to fetch files');
                    }
                }
            }
        } catch (err) {
            setError('Connection to backend lost');
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (isOpen) {
            fetchFiles();
        }
    }, [isOpen]);

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        if (isAuthenticated) fetchFiles(searchQuery);
    };

    const handleAuthorize = async () => {
        setIsAuthorizing(true);
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.start_google_auth === 'function') {
                const result = await api.start_google_auth() as { success: boolean };
                if (result.success) {
                    setIsAuthenticated(true);
                    fetchFiles();
                } else {
                    setError('Authorization failed. Please try again.');
                }
            }
        } catch {
            setError('Failed to start auth flow');
        } finally {
            setIsAuthorizing(false);
        }
    };

    const handleSelectFile = async (file: DriveFile) => {
        setSelectingId(file.id);
        try {
            const api = window.pywebview?.api;
            if (api && typeof api.get_drive_file_content === 'function') {
                const result = await api.get_drive_file_content(file.id, file.mimeType) as { success: boolean; content: string; error?: string };
                if (result.success) {
                    onSelect({
                        name: file.name,
                        content: result.content
                    });
                    onClose();
                } else {
                    setError(`Failed to load content: ${result.error}`);
                }
            }
        } catch (err) {
            setError('Failed to download file');
            console.error(err);
        } finally {
            setSelectingId(null);
        }
    };

    const getFileIcon = (mimeType: string) => {
        if (mimeType === 'application/vnd.google-apps.document') return <SiGoogledocs className="drive-icon drive-icon--docs" />;
        if (mimeType === 'application/vnd.google-apps.spreadsheet') return <SiGooglesheets className="drive-icon drive-icon--sheets" />;
        if (mimeType === 'application/vnd.google-apps.folder') return <Folder className="drive-icon drive-icon--folder" />;
        if (mimeType.includes('pdf')) return <FileText className="drive-icon drive-icon--pdf" />;
        if (mimeType.includes('image')) return <File className="drive-icon drive-icon--image" />;
        return <File className="drive-icon" />;
    };

    const formatDate = (dateStr: string) => {
        return new Date(dateStr).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
        });
    };

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
                    className="drive-modal"
                    initial={{ scale: 0.9, opacity: 0, y: 20 }}
                    animate={{ scale: 1, opacity: 1, y: 0 }}
                    exit={{ scale: 0.9, opacity: 0, y: 20 }}
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="drive-modal__header">
                        <div className="header-title">
                            <SiGoogledrive size={22} color="#34A853" />
                            <h2>Google Drive</h2>
                        </div>
                        <button className="close-btn" onClick={onClose}>
                            <X size={20} />
                        </button>
                    </div>

                    <div className="drive-modal__toolbar">
                        <form className="drive-search" onSubmit={handleSearch}>
                            <Search size={18} className="search-icon" />
                            <input 
                                type="text" 
                                placeholder="Search your Drive..." 
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                            {searchQuery && (
                                <button type="button" className="clear-search" onClick={() => { setSearchQuery(''); fetchFiles(''); }}>
                                    <X size={14} />
                                </button>
                            )}
                        </form>
                    </div>

                    <div className="drive-modal__content">
                        {error && (
                            <div className="drive-error">
                                <span>{error}</span>
                                <button onClick={() => fetchFiles(searchQuery)}>Retry</button>
                            </div>
                        )}

                        {loading && !selectingId && !isAuthorizing ? (
                            <div className="drive-loading">
                                <Loader2 size={32} className="spinning" />
                                <span>Accessing Drive...</span>
                            </div>
                        ) : isAuthenticated === false ? (
                            <div className="drive-auth-prompt">
                                <div className="auth-icon-stack">
                                    <Cloud size={48} className="auth-bg-icon" />
                                    <SiGoogledrive size={32} className="auth-main-icon" />
                                </div>
                                <h3>Connect to Google Drive</h3>
                                <p>Chad needs permission to read your documents and files from Google Drive.</p>
                                <button 
                                    className={`auth-btn ${isAuthorizing ? 'auth-btn--loading' : ''}`}
                                    onClick={handleAuthorize}
                                    disabled={isAuthorizing}
                                >
                                    {isAuthorizing ? <Loader2 size={18} className="spinning" /> : <ExternalLink size={18} />}
                                    <span>{isAuthorizing ? 'Waiting for Auth...' : 'Authorize Chad'}</span>
                                </button>
                                <span className="auth-help">This will open a browser window for a secure login.</span>
                            </div>
                        ) : (
                            <div className="drive-files">
                                {files.length === 0 && !error && !loading ? (
                                    <div className="drive-empty">
                                        <Cloud size={48} />
                                        <p>No files found</p>
                                    </div>
                                ) : (
                                    files.map(file => (
                                        <div 
                                            key={file.id} 
                                            className={`drive-file-item ${selectingId === file.id ? 'drive-file-item--selecting' : ''}`}
                                            onClick={() => !selectingId && handleSelectFile(file)}
                                        >
                                            <div className="file-icon-wrapper">
                                                {getFileIcon(file.mimeType)}
                                            </div>
                                            <div className="file-info">
                                                <span className="file-name">{file.name}</span>
                                                <span className="file-meta">
                                                    Modified {formatDate(file.modifiedTime)}
                                                </span>
                                            </div>
                                            <div className="file-actions">
                                                {selectingId === file.id ? (
                                                    <Loader2 size={18} className="spinning" />
                                                ) : (
                                                    <Download size={16} className="file-download-icon" />
                                                )}
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        )}
                    </div>

                    <div className="drive-modal__footer">
                        <p>Showing latest files from your Google Workspace.</p>
                        {files.length > 0 && <span className="file-count">{files.length} items</span>}
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
