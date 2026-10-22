import { useState } from 'react';
import { motion } from 'framer-motion';
import { FolderOpen, Check, X, AlertTriangle } from 'lucide-react';
import Modal from '../base/Modal';
import './FilePlanApprovalModal.css';

interface FilePlanAction {
    action: string;
    source?: string;
    destination?: string;
}

interface FilePlanData {
    planId: string;
    approvalToken: string;
    targetFolder: string;
    planSummary: string;
    actions: FilePlanAction[];
    formattedPlan: string;
}

interface FilePlanApprovalModalProps {
    isOpen: boolean;
    onClose: () => void;
    planData: FilePlanData | null;
    onApprove: (token: string) => void;
    onReject: () => void;
}

export default function FilePlanApprovalModal({
    isOpen,
    onClose,
    planData,
    onApprove,
    onReject
}: FilePlanApprovalModalProps) {
    const [isApproving, setIsApproving] = useState(false);

    if (!planData) return null;

    const handleApprove = async () => {
        setIsApproving(true);
        try {
            await onApprove(planData.approvalToken);
        } finally {
            setIsApproving(false);
            onClose();
        }
    };

    const handleReject = () => {
        onReject();
        onClose();
    };

    // Count actions by type
    const actionCounts = planData.actions.reduce((acc, action) => {
        acc[action.action] = (acc[action.action] || 0) + 1;
        return acc;
    }, {} as Record<string, number>);

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="📂 File Organization Plan">
            <div className="file-plan-modal">
                {/* Warning Banner */}
                <div className="file-plan-warning">
                    <AlertTriangle size={18} />
                    <span>Review carefully before approving. These changes cannot be undone.</span>
                </div>

                {/* Plan Summary */}
                <div className="file-plan-summary">
                    <div className="file-plan-goal">
                        <strong>Goal:</strong> {planData.planSummary}
                    </div>
                    <div className="file-plan-folder">
                        <FolderOpen size={16} />
                        <span>{planData.targetFolder}</span>
                    </div>
                </div>

                {/* Action Stats */}
                <div className="file-plan-stats">
                    {Object.entries(actionCounts).map(([action, count]) => (
                        <div key={action} className={`stat-badge stat-${action}`}>
                            <span className="stat-count">{count}</span>
                            <span className="stat-label">{action}</span>
                        </div>
                    ))}
                </div>

                {/* Formatted Plan (Scrollable) */}
                <div className="file-plan-details">
                    <pre>{planData.formattedPlan}</pre>
                </div>

                {/* Approval Token Display */}
                <div className="file-plan-token">
                    <span className="token-label">Approval Code:</span>
                    <code className="token-value">{planData.approvalToken}</code>
                </div>

                {/* Action Buttons */}
                <div className="file-plan-actions">
                    <motion.button
                        className="file-plan-btn btn-reject"
                        onClick={handleReject}
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                    >
                        <X size={18} />
                        Reject
                    </motion.button>
                    <motion.button
                        className="file-plan-btn btn-approve"
                        onClick={handleApprove}
                        disabled={isApproving}
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                    >
                        <Check size={18} />
                        {isApproving ? 'Approving...' : 'Approve Plan'}
                    </motion.button>
                </div>
            </div>
        </Modal>
    );
}
