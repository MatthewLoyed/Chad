import type { CSSProperties } from 'react';
import './ChadAvatar.css';
import { ChadBuddySvg } from './ChadBuddySvg';
import { useChadBuddyMode } from './useChadBuddyMode';
import type { ChadBuddyMode } from './types';

const HERO_SIZE = 88;
const DEFAULT_SIZE = 24;

export interface ChadAvatarProps {
    /** Render size in pixels. Defaults: 24 (default), 88 (hero). */
    size?: number;
    /** When set, renders the avatar with a name label (e.g. next to chat messages). */
    label?: string;
    /** Larger label spacing and weight for chat headers / hero placements. */
    prominent?: boolean;
    /** `hero` — large chat-header placement with a fixed minimum box size. */
    variant?: 'default' | 'hero';
    /**
     * Buddy animation mode. Omit to use the live mode for the active conversation
     * (see useChadBuddyMode). Pass `idle` for static placements (search, history).
     */
    mode?: ChadBuddyMode;
}

/** Presentational Chad buddy — pass an explicit mode or use ChadAvatarLive. */
export function ChadAvatar({
    size,
    label,
    prominent,
    variant = 'default',
    mode = 'idle',
}: ChadAvatarProps) {
    const resolvedSize = size ?? (variant === 'hero' ? HERO_SIZE : DEFAULT_SIZE);
    const modeClass = mode !== 'idle' ? ` chad-avatar--${mode}` : '';
    const variantClass = variant === 'hero' ? ' chad-avatar--hero' : '';
    const sizeStyle = { '--chad-avatar-size': `${resolvedSize}px` } as CSSProperties;

    const figure = (
        <span className="chad-avatar__figure" aria-hidden="true">
            <ChadBuddySvg size={resolvedSize} />
        </span>
    );

    if (!label) {
        return (
            <span className={`chad-avatar${modeClass}${variantClass}`} style={sizeStyle}>
                {figure}
            </span>
        );
    }

    return (
        <div
            className={`chad-avatar chad-avatar--labeled${prominent ? ' chad-avatar--prominent' : ''}${variantClass}${modeClass}`}
            style={sizeStyle}
        >
            {figure}
            <span className="chad-avatar__label">{label}</span>
        </div>
    );
}

/** Chad buddy wired to the active conversation's stream — ignores other chats / autonomous runs. */
export function ChadAvatarLive(props: Omit<ChadAvatarProps, 'mode'>) {
    const mode = useChadBuddyMode();
    return <ChadAvatar {...props} mode={mode} />;
}

export default ChadAvatarLive;
