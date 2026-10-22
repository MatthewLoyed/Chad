import { type ReactNode } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { useUiMode, useAppStore } from '../../store';
import './ChatPanel.css';

interface ChatPanelProps {
    orb: ReactNode;
    chat: ReactNode;
    input: ReactNode;
    controls?: ReactNode;
    status: string;
}

export default function ChatPanel({
    orb,
    chat,
    input,
    status,
}: ChatPanelProps) {
    const uiMode = useUiMode();
    const isSplash = uiMode === 'splash';

    const apiReady = useAppStore(state => state.apiReady);
    const appStatus = useAppStore(state => state.status);
    const isChatSyncing = useAppStore(state => state.isChatSyncing);
    const isOffline = !apiReady || appStatus === 'initializing' || appStatus === 'reconnecting' || appStatus === 'error';
    // "error" is the only state that should read as alarming — initializing/reconnecting
    // are expected, calm states (first boot, brief network blip) and use neutral styling.
    const isAlarmed = appStatus === 'error';

    // Get time-based greeting
    const getGreeting = () => {
        const hour = new Date().getHours();
        if (hour < 12) return 'Morning';
        if (hour < 17) return 'Afternoon';
        return 'Evening';
    };

    return (
        <div className={`chat-panel ${isSplash ? 'chat-panel--splash' : ''}`}>

            <div className="chat-panel__content">
                <AnimatePresence mode="wait">
                    {isSplash && (
                        <motion.div 
                            key="splash"
                            className="chat-panel__splash-header"
                            initial={{ opacity: 0, y: -20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20, transition: { duration: 0.2 } }}
                        >
                            <div className="chat-panel__orb-section">
                                <div className="chat-panel__orb-container">
                                    {orb}
                                </div>
                                <motion.h1 
                                    className="chat-panel__greeting"
                                >
                                    {getGreeting()}
                                </motion.h1>

                                <AnimatePresence>
                                    {isOffline && (
                                        <motion.div 
                                            className="chat-panel__status-subtle"
                                            initial={{ opacity: 0, y: 10 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            exit={{ opacity: 0 }}
                                            transition={{ duration: 0.3 }}
                                        >
                                            <span className={`status-pulse-dot ${isAlarmed ? 'offline' : 'connecting'}`} />
                                            <span className="status-text">
                                                {isAlarmed ? "Can't reach Chad right now" : 'Connecting to Chad...'}
                                            </span>
                                            <button
                                                className="chat-panel__status-reconnect-btn"
                                                onClick={() => {
                                                    useAppStore.getState().setStatus('reconnecting');
                                                    useAppStore.getState().setStatusText('Trying to reconnect...');
                                                    void window.pywebview?.api?.reconnect_server?.();
                                                }}
                                            >
                                                Try again
                                            </button>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {!isSplash && (
                    <div className="chat-panel__chat-container">
                        {isChatSyncing && (
                            <div className="chat-panel__sync-bar" role="progressbar" aria-label="Syncing conversation" />
                        )}
                        <AnimatePresence>
                            {isOffline && (
                                <motion.div 
                                    className="chat-panel__reconnect-banner"
                                    initial={{ height: 0, opacity: 0, y: -20 }}
                                    animate={{ height: 'auto', opacity: 1, y: 0 }}
                                    exit={{ height: 0, opacity: 0, y: -20 }}
                                    transition={{ duration: 0.3, ease: 'easeInOut' }}
                                >
                                    <div className={`chat-panel__reconnect-content ${isAlarmed ? 'chat-panel__reconnect-content--alarmed' : ''}`}>
                                        {isAlarmed ? (
                                            <AlertTriangle className="chat-panel__reconnect-icon" size={14} />
                                        ) : (
                                            <Loader2 className="chat-panel__reconnect-icon chat-panel__reconnect-icon--spin" size={14} />
                                        )}
                                        <span className="chat-panel__reconnect-text">
                                            {isAlarmed ? "Can't reach Chad — still trying" : 'Reconnecting to Chad...'}
                                        </span>
                                        <button
                                            className="chat-panel__banner-reconnect-btn"
                                            onClick={() => {
                                                useAppStore.getState().setStatus('reconnecting');
                                                useAppStore.getState().setStatusText('Trying to reconnect...');
                                                void window.pywebview?.api?.reconnect_server?.();
                                            }}
                                        >
                                            Try again
                                        </button>
                                    </div>
                                    <span className="chat-panel__reconnect-subtext">
                                        {status || 'Retrying...'}
                                    </span>
                                </motion.div>
                            )}
                        </AnimatePresence>
                        <motion.div
                            className="chat-panel__chat"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.1 }}
                        >
                            {chat}
                        </motion.div>
                    </div>
                )}
            </div>

            {/* Input Area - Always at bottom in flow, but centered via flex in splash */}
            <div className={`chat-panel__input ${isSplash ? 'chat-panel__input--splash' : ''}`}>
                {input}
            </div>
        </div>
    );
}
