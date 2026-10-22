/**
 * Shared loading / error / empty states.
 * ======================================
 *
 * Every pane that fetches from the Brain used to hand-roll these, and most of
 * them only had *two* of the three: a spinner and an empty state. A failed
 * fetch was `console.error`'d and then rendered as the empty state, so "you
 * have no routines" and "Chad is unreachable" looked identical, and a pane
 * whose guard returned early before clearing `loading` sat on its spinner
 * forever.
 *
 * The three states are distinct and must stay distinct:
 *
 *   loading — request in flight. Says what is being fetched.
 *   error   — request FAILED. Alert icon, plain-language cause, Retry action.
 *   empty   — request SUCCEEDED and returned nothing. Icon, heading, next step.
 *
 * Rule of thumb: an empty state is a successful outcome, so it never offers
 * "Try again" — it offers the action that creates the first item. An error
 * state always offers a retry, and never shows raw exception text.
 */
import type { ComponentType, ReactNode } from 'react';
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import { describeError } from '../../utils/describeError';
import './AsyncState.css';

interface IconProps { size?: number | string }

import { SkeletonCard, Skeleton, SkeletonCircle, SkeletonText, type SkeletonProps } from './Skeleton';
export { Skeleton, SkeletonCircle, SkeletonText, SkeletonCard, type SkeletonProps };

/** Request in flight. Supports traditional spinner or modern shimmering skeleton cards. */
export function LoadingState({
    label,
    variant = 'spinner',
    count = 3,
    hasAvatar = false,
}: {
    label?: string;
    variant?: 'spinner' | 'skeleton';
    count?: number;
    hasAvatar?: boolean;
}) {
    if (variant === 'skeleton') {
        return <SkeletonState count={count} hasAvatar={hasAvatar} />;
    }
    return (
        <div className="async-state async-state--loading" role="status" aria-live="polite">
            <Loader2 size={18} className="async-state__spinner" />
            {label && <span className="async-state__hint">{label}</span>}
        </div>
    );
}

/** Pre-composed skeleton loading state for lists and card collections. */
export function SkeletonState({
    count = 3,
    hasAvatar = false,
    lines = 2,
    className = '',
}: {
    count?: number;
    hasAvatar?: boolean;
    lines?: number;
    className?: string;
}) {
    return (
        <div className={`async-state-skeleton-group ${className}`} role="status" aria-live="polite">
            {Array.from({ length: count }).map((_, idx) => (
                <SkeletonCard key={idx} hasAvatar={hasAvatar} lines={lines} />
            ))}
        </div>
    );
}

/**
 * The fetch failed. Always renders a retry — recovery is the whole point.
 * `title` should name the thing that failed ("Couldn't load automations").
 */
export function ErrorState({
    title,
    error,
    onRetry,
    retrying = false,
}: {
    title: string;
    error?: unknown;
    onRetry?: () => void;
    retrying?: boolean;
}) {
    return (
        <div className="async-state async-state--error" role="alert">
            <div className="async-state__icon async-state__icon--danger">
                <AlertTriangle size={22} />
            </div>
            <span className="async-state__title">{title}</span>
            <span className="async-state__hint">{describeError(error)}</span>
            {onRetry && (
                <button className="async-state__retry" onClick={onRetry} disabled={retrying}>
                    <RefreshCw size={13} className={retrying ? 'async-state__spinner' : undefined} />
                    {retrying ? 'Retrying…' : 'Try again'}
                </button>
            )}
        </div>
    );
}

/** The fetch succeeded and there is genuinely nothing yet. */
export function EmptyState({
    icon: Icon,
    title,
    hint,
    action,
}: {
    icon?: ComponentType<IconProps>;
    title: string;
    hint?: string;
    action?: ReactNode;
}) {
    return (
        <div className="async-state async-state--empty">
            {Icon && (
                <div className="async-state__icon">
                    <Icon size={24} />
                </div>
            )}
            <span className="async-state__title">{title}</span>
            {hint && <span className="async-state__hint">{hint}</span>}
            {action}
        </div>
    );
}
