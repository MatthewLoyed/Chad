import { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Mic, MicOff, Square, X, Plus, Send, FileText, Paperclip, Image as ImageIcon, Loader2, AlertCircle, Zap, MessageSquare, Sparkles, ListTodo, ChevronDown, BrainCircuit } from 'lucide-react';
import { SiGoogledrive } from 'react-icons/si';
import { useAppStore, useStatus, useActiveModel, useChatTurnMode, useActiveChatBusy, usePlannerEnabled } from '../../store';
import SlashCommandMenu from './SlashCommandMenu';
import { useSlashCommands } from './useSlashCommands';
import { brainSocket } from '../../transport/brainSocket';
import { DriveBrowserModal } from '../modals';
import ModelSelector from './ModelSelector';
import TokenUsageTicker from '../data-display/TokenUsageTicker';
import './ChatInput.css';
import { turnModeCategory } from '../../utils/turn-mode';

export interface AttachedFile {
    id: string;
    file: File;
    name: string;
    size: number;
    type: 'text' | 'image' | 'document' | 'binary';
    status: 'processing' | 'ready' | 'error';
    content?: string; // Text content or Base64
    mediaType?: string;
    error?: string;
}

interface ChatInputProps {
    onSend: (text: string, contentBlocks?: unknown[], attachments?: AttachedFile[], category?: string, thinkingEnabled?: boolean) => void;
    disabled?: boolean;
    placeholder?: string;
}

interface DictationResult {
    success: boolean;
    text?: string;
    message?: string;
    session?: number;
    reason?: string;
    status?: 'transcribing' | string;
}

export default function ChatInput({ onSend, disabled = false, placeholder = 'Ask Chad...' }: ChatInputProps) {
    const [value, setValue] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
    const [lightboxImage, setLightboxImage] = useState<string | null>(null);
    const [isListening, setIsListening] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const [micError, setMicError] = useState<string | null>(null);
    const micErrorTimeoutRef = useRef<number | null>(null);
    const isListeningRef = useRef(false);
    const dictationSessionRef = useRef<number | null>(null);
    const dictationAppliedSessionRef = useRef<number | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const status = useStatus();
    const isBusy = useActiveChatBusy();
    const [showAttachmentMenu, setShowAttachmentMenu] = useState(false);
    const [driveBrowserOpen, setDriveBrowserOpen] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    
    // Chat Mode Selector State
    const chatMode = useChatTurnMode();
    const setChatMode = useAppStore((state) => state.setChatTurnMode);
    const plannerEnabled = usePlannerEnabled();

    useEffect(() => {
        const api = window.pywebview?.api;
        if (api && typeof api.set_ui_turn_mode === 'function') {
            api.set_ui_turn_mode(chatMode);
        } else {
            brainSocket.sendUiTurnMode(chatMode);
        }
    }, [chatMode]);

    const flashMicError = (message: string) => {
        setMicError(message);
        if (micErrorTimeoutRef.current) window.clearTimeout(micErrorTimeoutRef.current);
        micErrorTimeoutRef.current = window.setTimeout(() => setMicError(null), 4000);
    };

    useEffect(() => {
        isListeningRef.current = isListening;
    }, [isListening]);

    const applyDictationResult = useCallback((result: DictationResult | null | undefined) => {
        if (!result) return false;
        const session = result.session;
        if (session != null) {
            if (
                dictationSessionRef.current != null
                && session !== dictationSessionRef.current
            ) {
                return false;
            }
        }

        if (result.status === 'transcribing') {
            setIsListening(false);
            setIsTranscribing(true);
            return true;
        }

        if (session != null && dictationAppliedSessionRef.current === session) {
            setIsListening(false);
            setIsTranscribing(false);
            return true;
        }
        if (session != null) {
            dictationAppliedSessionRef.current = session;
        }

        setIsListening(false);
        setIsTranscribing(false);
        const text = result.text?.trim();
        if (text) {
            setValue(prev => prev ? `${prev} ${text}` : text);
        } else if (result.message) {
            flashMicError(result.message);
        }
        return true;
    }, []);

    // Silence / max-duration auto-stop pushes the transcript from Python so the
    // composer can leave listening without a second mic click.
    useEffect(() => {
        window.handleDictationResult = (payload) => {
            applyDictationResult(payload as DictationResult);
        };
        return () => {
            delete window.handleDictationResult;
        };
    }, [applyDictationResult]);

    // Stop an in-progress recording if the composer unmounts (navigation, route
    // switch) so the mic and the paused wake-word loop don't stay live forever.
    useEffect(() => {
        return () => {
            if (micErrorTimeoutRef.current) window.clearTimeout(micErrorTimeoutRef.current);
            if (isListeningRef.current) {
                window.pywebview?.api?.stop_dictation?.().catch(() => {});
            }
        };
    }, []);

    const [isModeMenuOpen, setIsModeMenuOpen] = useState(false);
    const modeSelectorRef = useRef<HTMLDivElement>(null);

    // Get active model info for capability-based routing
    const activeModel = useActiveModel();
    const isMultimodal = activeModel?.mode === 'cloud' || (activeModel?.provider === 'google' && activeModel?.name?.includes('gemini'));

    // Thinking toggle — only shown when the active model has an explicit thinking config
    const [thinkingOn, setThinkingOn] = useState<boolean>(false);
    const showThinkingToggle = activeModel?.thinking_enabled !== null && activeModel?.thinking_enabled !== undefined;
    // Sync default state whenever the active model changes
    useEffect(() => {
        if (activeModel?.thinking_enabled !== null && activeModel?.thinking_enabled !== undefined) {
            setThinkingOn(activeModel.thinking_enabled);
        }
    }, [activeModel?.name, activeModel?.thinking_enabled]);

    // Stop only reflects the turn owned by the visible chat; background sessions
    // must not light up controls in the current composer.

    const apiReady = useAppStore(state => state.apiReady);
    const isOffline = !apiReady || status === 'initializing' || status === 'error';

    // Fast-track ("/<alias>") autocomplete — allowlisted commands pushed at AUTH success.
    const fastTrackCommands = useAppStore(state => state.fastTrackCommands);
    const slashMenu = useSlashCommands(value, (text) => {
        setValue(text);
        textareaRef.current?.focus();
    }, fastTrackCommands);

    useEffect(() => {
        const insertSkill = (event: Event) => {
            const name = (event as CustomEvent<string>).detail;
            if (typeof name !== 'string' || !/^[\w-]+$/.test(name)) return;
            setValue(current => `/${name.replaceAll('_', '-')} ${current}`);
            requestAnimationFrame(() => textareaRef.current?.focus());
        };
        window.addEventListener('chad:compose-skill', insertSkill);
        return () => window.removeEventListener('chad:compose-skill', insertSkill);
    }, []);

    // --- FILE PROCESSING HELPERS ---

    const fileToBase64 = (file: File): Promise<string> => {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result as string;
                // Strip the data:image/png;base64, prefix
                const base64 = result.split(',')[1];
                resolve(base64);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    };

    const extractDocxText = async (file: File): Promise<string> => {
        try {
            const arrayBuffer = await file.arrayBuffer();
            // Lazy-load mammoth so it stays out of the first-paint bundle
            const mammoth = await import('mammoth');
            // Handle both import * as mammoth and import mammoth from 'mammoth'
            const mammothObj = mammoth as unknown as Record<string, unknown>;
            const extractor = (mammothObj.extractRawText || (mammothObj.default as Record<string, unknown>)?.extractRawText) as ((options: { arrayBuffer: ArrayBuffer }) => Promise<{ value: string }>) | undefined;
            
            if (!extractor) {
                throw new Error('Mammoth extraction function not found');
            }

            const result = await extractor({ arrayBuffer });
            if (!result.value || result.value.trim().length === 0) {
                throw new Error('Document appears to be empty or unreadable');
            }
            return result.value;
        } catch (err) {
            console.error('[ChatInput] Docx extraction failed:', err);
            throw err;
        }
    };

    const extractPdfText = async (file: File): Promise<string> => {
        const arrayBuffer = await file.arrayBuffer();
        // Lazy-load pdf.js so it stays out of the first-paint bundle
        const pdfjsLib = await import('pdfjs-dist');
        // Configure PDF.js worker - Use jsDelivr for reliable v5.x access
        pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${pdfjsLib.version}/build/pdf.worker.min.mjs`;
        const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
        let text = '';
        for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const content = await page.getTextContent();
            text += content.items.map((item: unknown) => (item as { str: string }).str).join(' ') + '\n';
        }
        return text;
    };

    const processFile = useCallback(async (file: File) => {
        const id = Math.random().toString(36).substring(7);
        const newFile: AttachedFile = {
            id,
            file,
            name: file.name,
            size: file.size,
            type: 'text', // Default
            status: 'processing'
        };

        setAttachedFiles(prev => [...prev, newFile]);
        console.log(`[ChatInput] Processing file: ${file.name}, isMultimodal: ${isMultimodal}`);

        try {
            // 1. Validation: Hard Limit (25MB)
            if (file.size > 25 * 1024 * 1024) {
                throw new Error('File exceeds 25MB limit');
            }

            const extension = file.name.split('.').pop()?.toLowerCase();
            const mimeType = file.type;

            // TRACK 1: Native Multimodal (Images/PDFs on Multimodal Models)
            if (mimeType.startsWith('image/')) {
                const base64 = await fileToBase64(file);
                updateFile(id, {
                    type: 'image',
                    status: 'ready',
                    content: base64,
                    mediaType: mimeType
                });
                return;
            }

            if (extension === 'pdf' && isMultimodal) {
                const base64 = await fileToBase64(file);
                updateFile(id, {
                    type: 'document',
                    status: 'ready',
                    content: base64,
                    mediaType: 'application/pdf'
                });
                return;
            }

            // TRACK 2: Binary / Archive (ZIP, etc.)
            const binaryExtensions = ['zip', 'rar', '7z', 'tar', 'gz', 'bin', 'exe', 'dll', 'so', 'dylib', 'pyc'];
            if (binaryExtensions.includes(extension || '')) {
                const base64 = await fileToBase64(file);
                updateFile(id, {
                    type: 'binary',
                    status: 'ready',
                    content: base64,
                    mediaType: mimeType || 'application/octet-stream'
                });
                return;
            }

            // TRACK 3: Text Extraction (Code/Docs/PDF Fallback)
            let text = '';
            if (extension === 'docx') {
                text = await extractDocxText(file);
            } else if (extension === 'pdf') {
                // Support/Warning Check for non-multimodal or explicit extraction
                text = await extractPdfText(file);
            } else {
                // Assume text-extractable for common formats
                text = await file.text();
            }

            updateFile(id, {
                type: 'text',
                status: 'ready',
                content: text
            });

        } catch (err) {
            console.error(`[ChatInput] Failed to process ${file.name}:`, err);
            updateFile(id, {
                status: 'error',
                error: (err as Error).message || 'Processing failed'
            });
        }
    }, [isMultimodal]);

    const updateFile = (id: string, updates: Partial<AttachedFile>) => {
        setAttachedFiles(prev => prev.map(f => f.id === id ? { ...f, ...updates } : f));
    };

    const removeFile = (id: string) => {
        setAttachedFiles(prev => prev.filter(f => f.id !== id));
    };

    const attachmentMenuRef = useRef<HTMLDivElement>(null);

    // --- EVENT HANDLERS ---

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as Node;
            if (attachmentMenuRef.current && !attachmentMenuRef.current.contains(target)) {
                setShowAttachmentMenu(false);
            }
            if (modeSelectorRef.current && !modeSelectorRef.current.contains(target)) {
                setIsModeMenuOpen(false);
            }
        };

        if (showAttachmentMenu || isModeMenuOpen) {
            document.addEventListener('mousedown', handleClickOutside);
        }

        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [showAttachmentMenu, isModeMenuOpen]);

    useEffect(() => {
        const textarea = textareaRef.current;
        if (textarea) {
            textarea.style.height = '0px';
            const scrollHeight = textarea.scrollHeight;
            const maxHeight = 320;
            const newHeight = Math.min(scrollHeight, maxHeight);
            textarea.style.height = `${newHeight}px`;
            textarea.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';
            if (scrollHeight > maxHeight) textarea.scrollTop = scrollHeight;
        }
    }, [value]);

    const handlePaste = async (e: React.ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            const file = item.getAsFile();
            if (file) {
                e.preventDefault();
                processFile(file);
            }
        }
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
        const files = Array.from(e.dataTransfer.files);
        files.forEach(processFile);
    };

    const handleSubmit = (e?: React.FormEvent) => {
        e?.preventDefault();

        // Sending a message abandons any in-progress recording rather than
        // leaving the mic (and the paused wake-word loop) hanging open.
        if (isListening) {
            setIsListening(false);
            window.pywebview?.api?.stop_dictation?.().catch(() => {});
        }

        if (isBusy) {
            console.log("[ DEBUG ] Stop button clicked. Triggering stop_active_task API...");
            window.pywebview?.api?.stop_active_task?.();
            return;
        }

        const hasReadyFiles = attachedFiles.some(f => f.status === 'ready');
        const isProcessing = attachedFiles.some(f => f.status === 'processing');

        if (isProcessing) return; // Wait for processing

        if ((value.trim() || hasReadyFiles) && !disabled) {
            navigator.vibrate?.(10);
            // Build Content Blocks
            const contentBlocks: unknown[] = [];
            
            // Add user text
            let finalPrompt = value.trim();
            if (finalPrompt) {
                contentBlocks.push({ type: 'text', text: finalPrompt });
            } else {
                // If no text, add a placeholder for the model
                const fileNames = attachedFiles.map(f => f.name).join(', ');
                finalPrompt = `[Attached: ${fileNames}]`;
            }

            // Route attachments
            attachedFiles.forEach(file => {
                if (file.status !== 'ready') return;

                if (file.type === 'text') {
                    console.log(`[ChatInput] Sending text extracted from ${file.name}: ${file.content?.substring(0, 100)}...`);
                    // Inject as XML-style block for models to understand file boundary
                    contentBlocks.push({
                        type: 'text',
                        text: `<file name="${file.name}">\n${file.content}\n</file>`
                    });
                } else if (file.type === 'image') {
                    contentBlocks.push({
                        type: 'image',
                        name: file.name,
                        source: {
                            type: 'base64',
                            media_type: file.mediaType || 'image/png',
                            data: file.content
                        }
                    });
                } else if (file.type === 'document' || file.type === 'binary') {
                    contentBlocks.push({
                        type: 'document',
                        name: file.name,
                        source: {
                            type: 'base64',
                            media_type: file.mediaType || (file.type === 'document' ? 'application/pdf' : 'application/octet-stream'),
                            data: file.content
                        }
                    });
                }
            });

            const readyFiles = attachedFiles.filter(f => f.status === 'ready');
            
            // Map UI modes to backend category flags
            const backendCategory = turnModeCategory(chatMode);
            
            onSend(finalPrompt, contentBlocks, readyFiles, backendCategory, showThinkingToggle ? thinkingOn : undefined);
            setValue('');
            setAttachedFiles([]);
            if (textareaRef.current) textareaRef.current.style.height = 'auto';
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (slashMenu.onKeyDown(e)) return;
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
    };

    const handleMicToggle = async (e: React.MouseEvent) => {
        e.stopPropagation();
        const api = window.pywebview?.api;
        if (!api?.start_dictation || !api?.stop_dictation) return;

        if (isListening) {
            setIsListening(false);
            setIsTranscribing(true);
            try {
                const result = await api.stop_dictation() as DictationResult;
                applyDictationResult(result);
            } catch (err) {
                console.error('[ChatInput] Dictation transcription failed:', err);
                flashMicError('Transcription failed.');
                setIsTranscribing(false);
            }
            return;
        }

        navigator.vibrate?.(10);
        try {
            const result = await api.start_dictation() as DictationResult;
            if (result?.success) {
                dictationSessionRef.current = result.session ?? null;
                dictationAppliedSessionRef.current = null;
                setIsListening(true);
            } else {
                flashMicError(result?.message || 'Could not start recording.');
            }
        } catch (err) {
            console.error('[ChatInput] Failed to start dictation:', err);
            flashMicError('Could not start recording.');
        }
    };

    const handleContainerClick = (e: React.MouseEvent) => {
        if (disabled) return;
        const target = e.target as HTMLElement;
        if (target.tagName === 'BUTTON' || target.closest('button') || target.closest('.chat-input__preview')) return;
        textareaRef.current?.focus();
    };



    return (
        <>
            <TokenUsageTicker />
            <form className="chat-input" onSubmit={handleSubmit}>
                <div
                    ref={containerRef}
                    className={`chat-input__container ${disabled ? 'chat-input__container--disabled' : ''} ${attachedFiles.length > 0 ? 'chat-input__container--has-attachments' : ''} ${isDragging ? 'chat-input__container--dragging' : ''}`}
                    onClick={handleContainerClick}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    <textarea
                        ref={textareaRef}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        onKeyDown={handleKeyDown}
                        onPaste={handlePaste}
                        placeholder={placeholder}
                        disabled={disabled}
                        className="chat-input__field"
                        rows={1}
                    />

                    <SlashCommandMenu menu={slashMenu} />

                    {/* Multimodal Preview Grid */}
                    <AnimatePresence>
                        {attachedFiles.length > 0 && (
                            <motion.div 
                                className="chat-input__previews"
                                initial={{ height: 0, opacity: 0 }}
                                animate={{ height: 'auto', opacity: 1 }}
                                exit={{ height: 0, opacity: 0 }}
                            >
                                {attachedFiles.map(file => (
                                    <div key={file.id} 
                                         className={`chat-input__preview chip-${file.type} status-${file.status} ${file.size > 10 * 1024 * 1024 ? 'size-warning' : ''}`}
                                         title={file.error}
                                    >
                                        <div className="chat-input__preview-content">
                                            {file.type === 'image' && file.status === 'ready' ? (
                                                <img 
                                                    src={`data:${file.mediaType};base64,${file.content}`} 
                                                    alt={file.name} 
                                                    onClick={() => setLightboxImage(`data:${file.mediaType};base64,${file.content}`)}
                                                />
                                            ) : (
                                                <div className="chat-input__file-icon">
                                                    {file.status === 'processing' ? <Loader2 className="animate-spin" size={20} /> : 
                                                     file.status === 'error' ? <AlertCircle color="#ff4d4d" size={20} /> :
                                                     file.type === 'document' || file.type === 'binary' ? <FileText size={20} /> : <ImageIcon size={20} />}
                                                </div>
                                            )}
                                            <div className="chat-input__file-info">
                                                <span className="chat-input__file-name">{file.name}</span>
                                                <span className="chat-input__file-size">{
                                                    file.size < 1024 * 1024 
                                                    ? `${(file.size / 1024).toFixed(0)}KB` 
                                                    : `${(file.size / 1024 / 1024).toFixed(1)}MB`
                                                }</span>
                                            </div>
                                        </div>
                                        <button
                                            type="button"
                                            className="chat-input__preview-remove"
                                            onClick={(e) => { e.stopPropagation(); removeFile(file.id); }}
                                        >
                                            <X size={12} />
                                        </button>
                                        
                                        {/* Status Overlays */}
                                        {file.status === 'error' && <div className="chat-input__preview-error-hint">Failed</div>}
                                        {file.size > 10 * 1024 * 1024 && file.status === 'ready' && <div className="chat-input__preview-size-hint">Large</div>}
                                    </div>
                                ))}
                            </motion.div>
                        )}
                    </AnimatePresence>

                    <div className="chat-input__toolbar">
                        <div className="chat-input__tools-left">
                             <div className="chat-input__attachment-wrapper" ref={attachmentMenuRef}>
                                <motion.button
                                    type="button"
                                    disabled={disabled}
                                    className={`chat-input__tool-btn ${disabled ? 'chat-input__tool-btn--disabled' : ''}`}
                                    whileHover={disabled ? {} : { scale: 1.05, backgroundColor: 'rgba(255,255,255,0.1)' }}
                                    whileTap={disabled ? {} : { scale: 0.95 }}
                                    onClick={(e) => { 
                                        if (disabled) return;
                                        e.stopPropagation(); 
                                        setShowAttachmentMenu(!showAttachmentMenu); 
                                    }}
                                >
                                    <Plus size={18} />
                                </motion.button>

                                <AnimatePresence>
                                    {showAttachmentMenu && (
                                        <motion.div
                                            className="chat-input__attachment-menu"
                                            initial={{ opacity: 0, scale: 0.9, y: 10 }}
                                            animate={{ opacity: 1, scale: 1, y: 0 }}
                                            exit={{ opacity: 0, scale: 0.9, y: 10 }}
                                            onClick={(e) => e.stopPropagation()}
                                        >
                                            <div className="chat-input__attachment-option" onClick={() => {
                                                const input = document.createElement('input');
                                                input.type = 'file';
                                                input.multiple = true;
                                                input.onchange = (e: Event) => {
                                                    const target = e.target as HTMLInputElement;
                                                    if (target.files) {
                                                        Array.from(target.files).forEach(processFile);
                                                    }
                                                };
                                                input.click();
                                                setShowAttachmentMenu(false);
                                            }}>
                                                <Paperclip size={18} />
                                                <span>Upload files</span>
                                            </div>
                                            <div className="chat-input__attachment-option" onClick={() => { setShowAttachmentMenu(false); setDriveBrowserOpen(true); }}>
                                                <SiGoogledrive size={18} color="#34A853" />
                                                <span>Google Drive</span>
                                            </div>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>

                            <div className="chat-input__mic-wrapper">
                                <motion.button
                                    type="button"
                                    onClick={(e) => {
                                        if (disabled || isOffline || isTranscribing) return;
                                        handleMicToggle(e);
                                    }}
                                    disabled={disabled || isOffline || isTranscribing}
                                    className={`chat-input__tool-btn ${isListening ? 'chat-input__tool-btn--active' : ''} ${(disabled || isOffline) ? 'chat-input__tool-btn--disabled' : ''}`}
                                    whileHover={(disabled || isOffline) ? {} : { scale: 1.05 }}
                                    whileTap={(disabled || isOffline) ? {} : { scale: 0.95 }}
                                    title={isOffline ? "Dictation unavailable offline" : isTranscribing ? "Transcribing…" : isListening ? "Stop and transcribe" : "Dictate"}
                                >
                                    {isTranscribing ? <Loader2 className="animate-spin" size={18} /> : isListening ? <Mic size={18} /> : <MicOff size={18} />}
                                </motion.button>

                                <AnimatePresence>
                                    {micError && (
                                        <motion.div
                                            className="chat-input__mic-error"
                                            initial={{ opacity: 0, y: 6 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            exit={{ opacity: 0, y: 6 }}
                                        >
                                            {micError}
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>

                            <div className="chat-input__divider" style={{ width: '1px', height: '20px', background: 'rgba(255,255,255,0.08)', margin: '0 2px' }} />

                            {/* Thinking Toggle — only shown for models with explicit thinking config */}
                            {showThinkingToggle && (
                                <motion.button
                                    type="button"
                                    disabled={disabled}
                                    onClick={(e) => { e.stopPropagation(); setThinkingOn(!thinkingOn); }}
                                    className={`chat-input__tool-btn ${thinkingOn ? 'chat-input__tool-btn--active' : ''} ${disabled ? 'chat-input__tool-btn--disabled' : ''}`}
                                    whileHover={disabled ? {} : { scale: 1.05 }}
                                    whileTap={disabled ? {} : { scale: 0.95 }}
                                    title={thinkingOn ? 'Thinking mode on — click to disable' : 'Thinking mode off — click to enable'}
                                >
                                    <BrainCircuit size={18} />
                                </motion.button>
                            )}

                            {/* Mode Selector Dropdown */}
                            <div className="chat-input__mode-selector" ref={modeSelectorRef}>
                                <motion.button
                                    type="button"
                                    disabled={disabled}
                                    onClick={(e) => {
                                        if (disabled) return;
                                        e.stopPropagation();
                                        setIsModeMenuOpen(!isModeMenuOpen);
                                    }}
                                    className={`chat-input__tool-btn chat-input__mode-trigger mode-${chatMode} ${isModeMenuOpen ? 'chat-input__mode-trigger--open' : ''} ${disabled ? 'chat-input__tool-btn--disabled' : ''}`}
                                    whileHover={disabled ? {} : { scale: 1.02 }}
                                    whileTap={disabled ? {} : { scale: 0.98 }}
                                    title="Select AI Operation Mode"
                                >
                                    {chatMode === 'agent' && <Zap size={16} className="mode-icon-agent" />}
                                    {chatMode === 'chat' && <MessageSquare size={16} className="mode-icon-chat" />}
                                    {chatMode === 'auto' && <Sparkles size={16} className="mode-icon-auto" />}
                                    {chatMode === 'plan' && <ListTodo size={16} className="mode-icon-plan" />}
                                    
                                    <span className="chat-input__mode-label">
                                        {chatMode === 'agent' && "Agent"}
                                        {chatMode === 'chat' && "Chat"}
                                        {chatMode === 'auto' && "Auto"}
                                        {chatMode === 'plan' && "Plan"}
                                    </span>
                                    
                                    <ChevronDown size={12} className={`chat-input__mode-chevron ${isModeMenuOpen ? 'rotated' : ''}`} />
                                </motion.button>

                                <AnimatePresence>
                                    {isModeMenuOpen && (
                                        <motion.div
                                            className="chat-input__mode-dropdown"
                                            initial={{ opacity: 0, scale: 0.95, y: 10 }}
                                            animate={{ opacity: 1, scale: 1, y: 0 }}
                                            exit={{ opacity: 0, scale: 0.95, y: 10 }}
                                            transition={{ duration: 0.12 }}
                                        >
                                            <button
                                                type="button"
                                                className={`chat-input__mode-item ${chatMode === 'auto' ? 'active' : ''}`}
                                                onClick={() => { setChatMode('auto'); setIsModeMenuOpen(false); }}
                                                title="Gemma classifies each message into Agent, Chat, or Plan"
                                            >
                                                <Sparkles size={15} />
                                                <div className="chat-input__mode-item-content">
                                                    <div className="chat-input__mode-item-title">Auto</div>
                                                    <div className="chat-input__mode-item-desc">Smart routing</div>
                                                </div>
                                            </button>

                                            <div className="chat-input__mode-divider" />

                                            {/* Agent Mode (Normal) */}
                                            <button
                                                type="button"
                                                className={`chat-input__mode-item ${chatMode === 'agent' ? 'active' : ''}`}
                                                onClick={() => { setChatMode('agent'); setIsModeMenuOpen(false); }}
                                            >
                                                <Zap size={15} className="mode-icon-agent" />
                                                <div className="chat-input__mode-item-content">
                                                    <div className="chat-input__mode-item-title">Agent Mode</div>
                                                    <div className="chat-input__mode-item-desc">Full tool use & actions</div>
                                                </div>
                                            </button>

                                            {/* Chat mode */}
                                            <button
                                                type="button"
                                                className={`chat-input__mode-item ${chatMode === 'chat' ? 'active' : ''}`}
                                                onClick={() => { setChatMode('chat'); setIsModeMenuOpen(false); }}
                                            >
                                                <MessageSquare size={15} className="mode-icon-chat" />
                                                <div className="chat-input__mode-item-content">
                                                    <div className="chat-input__mode-item-title">Chat</div>
                                                    <div className="chat-input__mode-item-desc">Fast replies, no tools</div>
                                                </div>
                                            </button>

                                            {plannerEnabled && (
                                                <>
                                                    <div className="chat-input__mode-divider" />

                                                    <button
                                                        type="button"
                                                        className={`chat-input__mode-item ${chatMode === 'plan' ? 'active' : ''}`}
                                                        onClick={() => { setChatMode('plan'); setIsModeMenuOpen(false); }}
                                                    >
                                                        <ListTodo size={15} className="mode-icon-plan" />
                                                        <div className="chat-input__mode-item-content">
                                                            <div className="chat-input__mode-item-title">Plan Mode</div>
                                                            <div className="chat-input__mode-item-desc">Execution breakdown</div>
                                                        </div>
                                                    </button>
                                                </>
                                            )}
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        </div>

                        <div className="chat-input__tools-right">
                            <ModelSelector />
                            <motion.button
                                type="submit"
                                disabled={!isBusy && (disabled || (!value.trim() && attachedFiles.length === 0))}
                                className={`chat-input__send-btn ${isBusy ? 'chat-input__send-btn--stop' : ''}`}
                            >
                                {isBusy ? <Square size={14} fill="currentColor" /> : <Send size={18} />}
                            </motion.button>
                        </div>
                    </div>
                </div>
            </form>

            <AnimatePresence>
                {lightboxImage && (
                    <motion.div
                        className="chat-input__lightbox"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setLightboxImage(null)}
                    >
                        <motion.img src={lightboxImage} initial={{ scale: 0.8 }} animate={{ scale: 1 }} />
                        <button className="chat-input__lightbox-close" onClick={() => setLightboxImage(null)}>
                            <X size={24} />
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>

            <DriveBrowserModal
                isOpen={driveBrowserOpen}
                onClose={() => setDriveBrowserOpen(false)}
                onSelect={(file) => processFile(new File([file.content], file.name, { type: 'text/plain' }))}
            />
        </>
    );
}
