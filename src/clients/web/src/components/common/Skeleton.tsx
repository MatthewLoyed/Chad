import type { CSSProperties, ReactNode } from 'react';
import './Skeleton.css';

export interface SkeletonProps {
    width?: string | number;
    height?: string | number;
    borderRadius?: string | number;
    className?: string;
    style?: CSSProperties;
}

/** Basic shimmering block */
export function Skeleton({
    width = '100%',
    height = '14px',
    borderRadius = '4px',
    className = '',
    style,
}: SkeletonProps) {
    return (
        <div
            className={`skeleton-base ${className}`}
            style={{
                width: typeof width === 'number' ? `${width}px` : width,
                height: typeof height === 'number' ? `${height}px` : height,
                borderRadius: typeof borderRadius === 'number' ? `${borderRadius}px` : borderRadius,
                ...style,
            }}
        />
    );
}

/** Shimmering circle (for avatars, badges, icons) */
export function SkeletonCircle({
    size = 28,
    className = '',
    style,
}: {
    size?: number;
    className?: string;
    style?: CSSProperties;
}) {
    return (
        <Skeleton
            width={size}
            height={size}
            borderRadius="50%"
            className={className}
            style={style}
        />
    );
}

/** Shimmering multiline paragraph with staggered line widths */
export function SkeletonText({
    lines = 3,
    lastLineWidth = '50%',
    lineHeight = 13,
    gap = 8,
    className = '',
}: {
    lines?: number;
    lastLineWidth?: string;
    lineHeight?: number;
    gap?: number;
    className?: string;
}) {
    const defaultWidths = ['92%', '80%', '65%', '75%'];
    return (
        <div className={`skeleton-text-group ${className}`} style={{ gap: `${gap}px` }}>
            {Array.from({ length: lines }).map((_, idx) => {
                const isLast = idx === lines - 1;
                const width = isLast ? lastLineWidth : defaultWidths[idx % defaultWidths.length];
                return (
                    <Skeleton
                        key={idx}
                        width={width}
                        height={lineHeight}
                        borderRadius={4}
                    />
                );
            })}
        </div>
    );
}

/** Pre-composed card skeleton */
export function SkeletonCard({
    hasAvatar = false,
    lines = 2,
    children,
    className = '',
}: {
    hasAvatar?: boolean;
    lines?: number;
    children?: ReactNode;
    className?: string;
}) {
    return (
        <div className={`skeleton-card ${hasAvatar ? 'skeleton-card--row' : ''} ${className}`}>
            {hasAvatar && <SkeletonCircle size={36} />}
            <div style={{ flex: 1, minWidth: 0 }}>
                {children || <SkeletonText lines={lines} />}
            </div>
        </div>
    );
}
