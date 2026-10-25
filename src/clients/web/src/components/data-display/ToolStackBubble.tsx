import React, { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, Loader2, Check, AlertTriangle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import ToolBubble from './ToolBubble';
import CollapseRail from '../common/CollapseRail';
import { resolveToolUI } from '../../utils/tool-ui';
import type { ThinkingEvent } from '../../store';
import './ToolStackBubble.css';

export interface StackedToolItem {
    correlationId: string;
    startEvent: ThinkingEvent;
    endEvent?: ThinkingEvent;
    status: 'pending' | 'success' | 'error' | 'awaiting';
    nestedChildren?: React.ReactNode;
}

interface ToolStackBubbleProps {
    category: string;
    items: StackedToolItem[];
}

export default function ToolStackBubble({ category, items }: ToolStackBubbleProps) {
    // The requirement specified "must be autoclosed by default"
    const [isExpanded, setIsExpanded] = useState(false);
    
    const firstItem = items[0];
    const ui = useMemo(() => firstItem ? resolveToolUI(firstItem.startEvent) : null, [firstItem]);

    // Calculate aggregate status across all items in the stack
    const aggStatus = useMemo(() => {
        if (items.some(i => i.status === 'error')) return 'error';
        if (items.some(i => i.status === 'awaiting')) return 'awaiting';
        if (items.some(i => i.status === 'pending')) return 'pending';
        return 'success';
    }, [items]);

    // Build inline timeline steps from items
    const timelineSteps = useMemo(() => items.slice(0, 5).map((item, i) => {
        const stepUi = resolveToolUI(item.startEvent);
        return { icon: stepUi.icon, status: item.status, key: item.correlationId || i };
    }), [items]);
    const hasMore = items.length > 5;

    // If only one item, render it as the native standard ToolBubble to preserve perfect fallback
    if (items.length === 1) {
        const evt = firstItem.endEvent 
            ? { ...firstItem.startEvent, ...firstItem.endEvent } 
            : firstItem.startEvent;
        return <ToolBubble event={evt} status={firstItem.status}>{firstItem.nestedChildren}</ToolBubble>;
    }

    const friendlyCategoryName = category
        .split(/[_\s]/)
        .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join(' ');

    // Claude-style user friendly descriptive labels
    const getHeaderLabel = () => {
        if (category === 'browser') return 'Web Research';
        if (category === 'filesystem') return 'File Operations';
        if (category === 'delegation') return 'Agent Orchestration';
        if (category === 'docs') return 'Document Editing';
        if (category === 'email') return 'Mail Sync';
        return `${friendlyCategoryName} Actions`;
    };

    return (
        <div className={`tool-stack-wrapper tool-stack--${aggStatus}`}>
            <div
                className="tool-stack__header"
                role="button"
                tabIndex={0}
                aria-expanded={isExpanded}
                onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setIsExpanded((expanded) => !expanded);
                    }
                }}
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="tool-stack__header-icon" style={{ color: ui?.color || '#6366F1' }}>
                    {ui?.icon}
                </div>

                <div className="tool-stack__label">
                    {getHeaderLabel()}
                </div>

                {/* Inline timeline */}
                <div className="tool-stack__timeline">
                    {timelineSteps.map((step, i) => (
                        <React.Fragment key={step.key}>
                            <span className={`tool-timeline-step tool-timeline-step--${step.status}`}>
                                {step.icon}
                            </span>
                            {i < timelineSteps.length - 1 && (
                                <span className="tool-timeline-arrow">›</span>
                            )}
                        </React.Fragment>
                    ))}
                    {hasMore && <span className="tool-timeline-more">+{items.length - 5}</span>}
                </div>

                <div className="tool-stack__chevron">
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </div>

                <div className="tool-stack__status-indicator">
                    {aggStatus === 'pending' ? <Loader2 className="spin-slow" size={13} /> :
                     aggStatus === 'error' ? <AlertTriangle size={13} className="alert-icon" /> :
                     <Check size={13} className="check-icon" />}
                </div>
            </div>

            <AnimatePresence>
                {isExpanded && (
                    <motion.div 
                        className="tool-stack__expanded-content"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2, ease: 'easeInOut' }}
                    >
                        <CollapseRail
                            className="tool-stack__list"
                            onCollapse={() => setIsExpanded(false)}
                            label="Collapse tool stack"
                        >
                            {items.map((item, idx) => {
                                const combinedEvt = item.endEvent 
                                    ? { ...item.startEvent, ...item.endEvent } 
                                    : item.startEvent;
                                return (
                                    <div key={item.correlationId || idx} className="tool-stack__nested-item">
                                        <ToolBubble event={combinedEvt} status={item.status}>
                                            {item.nestedChildren}
                                        </ToolBubble>
                                    </div>
                                );
                            })}
                        </CollapseRail>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
