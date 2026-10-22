import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../../store';
import { Zap, Cpu, Hash, Maximize2, Timer } from 'lucide-react';
import './TokenUsageTicker.css';

export default function TokenUsageTicker() {
    const chatMessages = useAppStore(state => state.chatMessages);
    const tokenUsage = useAppStore(state => state.tokenUsage);

    const prompt_tokens = tokenUsage?.prompt_tokens;
    const completion_tokens = tokenUsage?.completion_tokens;
    const total_tokens = tokenUsage?.total_tokens;
    const reasoning_tokens = tokenUsage?.reasoning_tokens;
    const context_window = tokenUsage?.context_window;
    const speed = tokenUsage?.speed;
    const ttft = tokenUsage?.ttft;
    const estimated_prompt_tokens = tokenUsage?.estimated_prompt_tokens;

    // Determine whether token statistics have actually been populated
    const hasPopulatedTokens = Boolean(
        tokenUsage && (
            (prompt_tokens != null && prompt_tokens > 0) ||
            (completion_tokens != null && completion_tokens > 0) ||
            (total_tokens != null && total_tokens > 0)
        )
    );

    // Simple format helper (e.g. 12500 -> 12.5k)
    const formatCount = (num: number | undefined | null) => {
        if (num === undefined || num === null || num === 0) return '--';
        if (num >= 1000) {
            return (num / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
        }
        return num.toString();
    };

    // Calculate percentage of context window used
    const percentage = (context_window && total_tokens) ? Math.min(100, (total_tokens / context_window) * 100) : null;

    // Hide ticker until a message has been sent and token counts are populated
    if (!chatMessages?.length || !tokenUsage || !hasPopulatedTokens) {
        return null;
    }

    return (
        <AnimatePresence>
            <motion.div 
                className="token-usage-ticker"
                initial={{ opacity: 0, y: 10, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
            >
                <div className="ticker-stat" title="Prompt Tokens">
                    <Cpu size={11} className="stat-icon" />
                    <span className="stat-label">Prompt:</span>
                    <span className="stat-value">
                        {formatCount(prompt_tokens)}
                        {(!prompt_tokens || prompt_tokens === 0) && estimated_prompt_tokens && estimated_prompt_tokens > 0 && (
                            <span className="stat-value-est" title="Estimated Prompt Tokens">
                                 (est: {formatCount(estimated_prompt_tokens)})
                            </span>
                        )}
                    </span>
                </div>

                <div className="ticker-divider" />

                <div className="ticker-stat" title="Completion Tokens">
                    <Zap size={11} className="stat-icon" />
                    <span className="stat-label">Resp:</span>
                    <span className="stat-value">{formatCount(completion_tokens)}</span>
                </div>

                {reasoning_tokens && reasoning_tokens > 0 && (
                    <>
                        <div className="ticker-divider" />
                        <div className="ticker-stat ticker-stat--reasoning" title="Reasoning Tokens">
                            <Hash size={11} className="stat-icon" />
                            <span className="stat-label">Reason:</span>
                            <span className="stat-value">{formatCount(reasoning_tokens)}</span>
                        </div>
                    </>
                )}

                {context_window && (
                    <>
                        <div className="ticker-divider" />
                        <div className="ticker-stat ticker-stat--limit" title={`Context limit: ${formatCount(context_window)}`}>
                            <Maximize2 size={11} className="stat-icon" />
                            <div className="limit-visual">
                                <div className="limit-track">
                                    <motion.div 
                                        className={`limit-fill ${percentage && percentage > 80 ? 'fill-danger' : percentage && percentage > 50 ? 'fill-warning' : ''}`}
                                        initial={{ width: 0 }}
                                        animate={{ width: `${percentage}%` }}
                                        transition={{ duration: 0.5, ease: "easeOut" }}
                                    />
                                </div>
                                <span className="limit-text">{percentage?.toFixed(1)}%</span>
                            </div>
                        </div>
                    </>
                )}

                {speed != null && speed > 0 && (
                    <>
                        <div className="ticker-divider" />
                        <div className="ticker-stat" title="Tokens per second">
                            <Zap size={11} className="stat-icon" />
                            <span className="stat-label">Speed:</span>
                            <span className="stat-value">{speed.toFixed(1)} t/s</span>
                        </div>
                    </>
                )}

                {ttft != null && ttft > 0 && (
                    <>
                        <div className="ticker-divider" />
                        <div className="ticker-stat" title="Time to first token">
                            <Timer size={11} className="stat-icon" />
                            <span className="stat-label">TTFT:</span>
                            <span className="stat-value">{ttft.toFixed(2)}s</span>
                        </div>
                    </>
                )}

                <div className="ticker-stat ticker-stat--total">
                    <span className="stat-label">Total:</span>
                    <span className="stat-value-total">{formatCount(total_tokens)}</span>
                </div>
            </motion.div>
        </AnimatePresence>
    );
}
