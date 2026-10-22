import { useEffect, useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bell, X } from 'lucide-react';
import { useAppStore } from '../store';
import type { ThinkingEvent } from '../store';
import './NotificationOverlay.css';

interface Notification extends ThinkingEvent {
    id: string;
    expiryAt: number;
}

const NOTIFICATION_DURATION = 8000; // 8 seconds

export default function NotificationOverlay() {
    const thinkingEvents = useAppStore(state => state.thinkingEvents);
    const [notifications, setNotifications] = useState<Notification[]>([]);
    const lastProcessedIndex = useRef(0);

    useEffect(() => {
        // Process ONLY new events from the store
        if (thinkingEvents.length > lastProcessedIndex.current) {
            const newEvents = thinkingEvents.slice(lastProcessedIndex.current);
            lastProcessedIndex.current = thinkingEvents.length;

            const autonomousEvents = newEvents.filter(e => e.type === 'autonomous_notification');
            
            if (autonomousEvents.length > 0) {
                const now = Date.now();
                const newNotifications = autonomousEvents.map(e => ({
                    ...e,
                    id: Math.random().toString(36).substr(2, 9),
                    expiryAt: now + NOTIFICATION_DURATION
                }));

                setNotifications(prev => [...prev, ...newNotifications]);
            }
        }
    }, [thinkingEvents]);

    // Timer to auto-prune expired notifications
    useEffect(() => {
        const interval = setInterval(() => {
            const now = Date.now();
            setNotifications(prev => prev.filter(n => n.expiryAt > now));
        }, 1000);
        return () => clearInterval(interval);
    }, []);

    const dismiss = (id: string) => {
        setNotifications(prev => prev.filter(n => n.id !== id));
    };

    return (
        <div className="notification-overlay">
            <AnimatePresence>
                {notifications.map((n) => (
                    <motion.div
                        key={n.id}
                        initial={{ opacity: 0, x: 50, scale: 0.9 }}
                        animate={{ opacity: 1, x: 0, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.2 } }}
                        className="notification-toast"
                    >
                        <div className="notification-toast__icon">
                            <Bell size={18} />
                        </div>
                        <div className="notification-toast__content">
                            <div className="notification-toast__header">
                                <span className="notification-toast__title">System Activity</span>
                                <button 
                                    className="notification-toast__close"
                                    onClick={() => dismiss(n.id)}
                                >
                                    <X size={14} />
                                </button>
                            </div>
                            <div className="notification-toast__body">
                                {n.text}
                            </div>
                        </div>
                        <div className="notification-toast__progress">
                           <motion.div 
                             className="notification-toast__progress-bar"
                             initial={{ width: "100%" }}
                             animate={{ width: "0%" }}
                             transition={{ duration: NOTIFICATION_DURATION / 1000, ease: "linear" }}
                           />
                        </div>
                    </motion.div>
                ))}
            </AnimatePresence>
        </div>
    );
}
