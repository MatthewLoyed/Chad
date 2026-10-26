import type { ChatTurnMode } from '../store';

/** The backend calls Plan mode "planning". Other modes use their UI names. */
export function turnModeCategory(mode: ChatTurnMode): string {
    return mode === 'plan' ? 'planning' : mode;
}

/** Older messages have no saved mode; keep the visible composer mode on retry. */
export function retryTurnCategory(saved: string | undefined, current: ChatTurnMode): string {
    return saved || turnModeCategory(current);
}
