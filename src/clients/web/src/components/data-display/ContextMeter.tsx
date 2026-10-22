import { useState, useEffect } from 'react';
import './ContextMeter.css';

interface ContextStatus {
    profile: string;
    limits: {
        soft: number;
        hard: number;
        max: number;
    };
    current_load: number;
}

export default function ContextMeter() {
    const [status, setStatus] = useState<ContextStatus | null>(null);

    const fetchStatus = () => {
        if (window.pywebview?.api?.get_context_status) {
            window.pywebview.api.get_context_status().then((res: unknown) => {
                const data = res as ContextStatus;
                if (data && data.limits) {
                    setStatus(data);
                }
            }).catch(console.error);
        }
    };

    useEffect(() => {
        // Initial fetch
        fetchStatus();

        // Poll every 5 seconds to keep synchronized with backend state
        const interval = setInterval(fetchStatus, 5000);
        return () => clearInterval(interval);
    }, []);

    const handleForceConsolidate = () => {
        if (window.pywebview?.api?.force_consolidation) {
            // Optimistic UI update or toast could go here
            console.log("Forcing consolidation...");
            window.pywebview.api.force_consolidation().then(() => {
                // Refresh status after a delay to allow backend to process
                setTimeout(fetchStatus, 2000);
            });
        }
    };

    if (!status) return null;

    const { current_load, limits } = status;
    // Calculate percentage based on HARD limit (100% = forced consolidation)
    const percentage = Math.min(100, Math.max(0, (current_load / limits.hard) * 100));

    // Determine color state
    let colorClass = 'ctx-safe';
    if (current_load > limits.hard) colorClass = 'ctx-critical';
    else if (current_load > limits.soft) colorClass = 'ctx-warning';

    return (
        <div className="context-meter-container">
            <div className="context-info">
                <div className="context-header">
                    <span className="ctx-title">Context Load</span>
                    <span className="ctx-profile-badge">{status.profile}</span>
                </div>
                <div className="ctx-stats">
                    <span className={`ctx-value ${colorClass}`}>
                        {current_load.toLocaleString()}
                    </span>
                    <span className="ctx-limit">
                        / {limits.hard.toLocaleString()} toks
                    </span>
                </div>
            </div>

            <div className="ctx-progress-track">
                <div
                    className={`ctx-progress-bar ${colorClass}`}
                    style={{ width: `${percentage}%` }}
                />
            </div>

            {/* Show Consolidation button only if load is significant or user wants control */}
            <div className="ctx-controls">
                <button
                    className="ctx-action-btn"
                    onClick={handleForceConsolidate}
                    title="Force immediate memory consolidation"
                >
                    Force Consolidate
                </button>
            </div>
        </div>
    );
}
