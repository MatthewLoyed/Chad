import { useEffect, useMemo, useState } from 'react';
import { resolveToolUI } from '../../utils/tool-ui';
import type { ThinkingEvent } from '../../store';
import './ToolMenuCarousel.css';

export interface ToolMenuData {
    tools: (string | { name?: string; tool_name?: string })[];
    shortlistSize?: number;
    selectionPath?: string;
}

interface ToolMenuCarouselProps {
    menu: ToolMenuData;
    isActive?: boolean;
}

/**
 * Mandatory core tools injected on every turn regardless of query.
 * Filtered out of the GUI active toolkit carousel so only extra / query-specific tools appear.
 */
export const CORE_TOOL_NAMES = new Set([
    'ask_user_question',
    'search_for_tool',
    'list_skills',
    'load_skill',
    'call_mcp_tool',
    'manage_connectors',
    'run_saved_routine',
    'manage_user_memory',
    'web_search',
]);

/**
 * Wait-theater chip carousel — shuffles through the resolved tool menu with icons
 * from tool-ui while the supervisor model is still loading.
 */
export default function ToolMenuCarousel({ menu, isActive = false }: ToolMenuCarouselProps) {
    const { tools, shortlistSize, selectionPath } = menu;
    const extraTools = useMemo(() => {
        const names = (tools || [])
            .map((t) => (typeof t === 'string' ? t : t?.name || t?.tool_name || ''))
            .filter((n): n is string => Boolean(n));
        return names.filter((name) => !CORE_TOOL_NAMES.has(name));
    }, [tools]);

    const [highlightIdx, setHighlightIdx] = useState(0);
    const rotating = isActive && extraTools.length > 1;

    // Rewind to the first chip whenever rotation stops, during render rather than inside
    // the interval effect below — same reason as useWaitTheaterStatus.
    const [wasRotating, setWasRotating] = useState(rotating);
    if (wasRotating !== rotating) {
        setWasRotating(rotating);
        if (!rotating) setHighlightIdx(0);
    }

    useEffect(() => {
        if (!rotating) {
            return;
        }
        const id = window.setInterval(() => {
            setHighlightIdx((i) => (i + 1) % extraTools.length);
        }, 900);
        return () => window.clearInterval(id);
    }, [rotating, extraTools.length]);

    const chips = useMemo(() => {
        return extraTools.map((name, idx) => {
            const stub: ThinkingEvent = { type: 'tool_start', name };
            const ui = resolveToolUI(stub);
            return { name, ui, idx };
        });
    }, [extraTools]);

    if (extraTools.length === 0) return null;

    const subtitle = shortlistSize && shortlistSize > extraTools.length
        ? `Picked ${extraTools.length} from ${shortlistSize} candidates`
        : `${extraTools.length} tool${extraTools.length === 1 ? '' : 's'} ready`;

    return (
        <div className={`tool-menu-carousel ${isActive ? 'tool-menu-carousel--active' : ''}`}>
            <div className="tool-menu-carousel__header">
                <span className="tool-menu-carousel__title">Toolkit for this turn</span>
                <span className="tool-menu-carousel__meta">{subtitle}</span>
            </div>
            <div className="tool-menu-carousel__track" aria-live="polite">
                {chips.map(({ name, ui, idx }) => (
                    <span
                        key={name}
                        className={`tool-menu-chip ${idx === highlightIdx && isActive ? 'tool-menu-chip--highlight' : ''}`}
                        style={{ '--chip-accent': ui.color } as React.CSSProperties}
                        title={ui.title}
                    >
                        <span className="tool-menu-chip__icon">{ui.icon}</span>
                        <span className="tool-menu-chip__label">{formatToolChipLabel(name)}</span>
                    </span>
                ))}
            </div>
            {selectionPath && isActive && (
                <span className="tool-menu-carousel__path">via {selectionPath.replace(/_/g, ' ')}</span>
            )}
        </div>
    );
}

function formatToolChipLabel(name: string): string {
    return name.replace(/^delegate_to_/, '').replace(/_/g, ' ');
}
