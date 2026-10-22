import React from 'react';
import { motion } from 'framer-motion';
import { AlertTriangle } from 'lucide-react';
import Modal from '../base/Modal';
import './ToolLimitModal.css';

interface ToolLimitModalProps {
    isOpen: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}

const ToolLimitModal: React.FC<ToolLimitModalProps> = ({ isOpen, onConfirm, onCancel }) => {
    return (
        <Modal isOpen={isOpen} onClose={onCancel} title="Safety Limit Reached">
            <div className="tool-limit-modal">
                <div className="tool-limit-modal__icon-wrapper">
                    <motion.div
                        initial={{ scale: 0.8, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        transition={{ delay: 0.2, type: 'spring', stiffness: 200 }}
                    >
                        <AlertTriangle size={48} strokeWidth={1.5} />
                    </motion.div>
                    <div className="tool-limit-modal__icon-bg" />
                </div>

                <motion.p
                    className="tool-limit-modal__message"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.3 }}
                >
                    The AI has executed the maximum number of safety-allowed tools (10) for this turn.
                </motion.p>

                <motion.p
                    className="tool-limit-modal__sub-message"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.4 }}
                >
                    This usually happens during complex tasks or infinite loops.
                    Do you want to authorize <span className="tool-limit-modal__highlight">10 more tool calls</span> to allow it to continue?
                </motion.p>

                <motion.div
                    className="tool-limit-modal__actions"
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.5, type: 'spring', stiffness: 100 }}
                >
                    <button
                        className="tool-limit-modal__btn tool-limit-modal__btn--cancel"
                        onClick={onCancel}
                    >
                        Cancel & Stop
                    </button>
                    <button
                        className="tool-limit-modal__btn tool-limit-modal__btn--primary"
                        onClick={onConfirm}
                    >
                        Continue Execution
                    </button>
                </motion.div>
            </div>
        </Modal>
    );
};

export default ToolLimitModal;
