import { motion, AnimatePresence } from 'framer-motion';
import './DeleteChatModal.css';

interface DeleteChatModalProps {
    isOpen: boolean;
    chatId: string | null;
    chatTitle: string | null;
    onClose: () => void;
    onConfirm: (id: string) => void;
}

export default function DeleteChatModal({ isOpen, chatId, chatTitle, onClose, onConfirm }: DeleteChatModalProps) {
    if (!isOpen || !chatId) return null;

    const handleConfirm = () => {
        onConfirm(chatId);
        onClose();
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="delete-modal-overlay" onClick={onClose}>
                    <motion.div 
                        className="delete-modal-content"
                        initial={{ opacity: 0, scale: 0.9, y: 10 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.9, y: 10 }}
                        transition={{ duration: 0.2, ease: "easeOut" }}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <h2 className="delete-modal-title">Delete chat?</h2>
                        
                        <div className="delete-modal-body">
                            <p>This will delete <strong>{chatTitle || 'this chat'}</strong>.</p>
                            <p className="delete-modal-subtext">
                                Visit <span className="settings-link">settings</span> to delete any memories saved during this chat.
                            </p>
                        </div>

                        <div className="delete-modal-footer">
                            <button className="delete-modal-btn delete-modal-btn--cancel" onClick={onClose}>
                                Cancel
                            </button>
                            <button className="delete-modal-btn delete-modal-btn--delete" onClick={handleConfirm}>
                                Delete
                            </button>
                        </div>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
}
