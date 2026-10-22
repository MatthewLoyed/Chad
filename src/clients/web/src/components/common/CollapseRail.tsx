import type { ReactNode } from 'react';
import './CollapseRail.css';

export interface CollapseRailProps {
    children: ReactNode;
    /** Collapse the parent section (same action as the header chevron). */
    onCollapse: () => void;
    /** Accessible name for the rail hit target. */
    label?: string;
    /**
     * When false, the rail is hidden and non-interactive (e.g. parent section
     * is closed but still mounted for CSS height animation).
     */
    active?: boolean;
    className?: string;
    bodyClassName?: string;
}

/**
 * Shared left gutter line for expandable chat blocks (thinking, tools,
 * subagents). Wide hitbox; purple glow on hover; click collapses.
 */
export default function CollapseRail({
    children,
    onCollapse,
    label = 'Collapse',
    active = true,
    className,
    bodyClassName,
}: CollapseRailProps) {
    return (
        <div
            className={['collapse-rail', !active && 'collapse-rail--inactive', className]
                .filter(Boolean)
                .join(' ')}
        >
            <button
                type="button"
                className="collapse-rail__hit"
                onClick={onCollapse}
                tabIndex={active ? 0 : -1}
                aria-label={label}
                title={label}
                aria-hidden={!active}
            />
            <div className={['collapse-rail__body', bodyClassName].filter(Boolean).join(' ')}>
                {children}
            </div>
        </div>
    );
}
