import type { ChatTurnMode } from '../store';

/** The backend calls Plan mode "planning". Other modes use their UI names. */
export function turnModeCategory(mode: ChatTurnMode): string {
    return mode === 'plan' ? 'planning' : mode;
}
