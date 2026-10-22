/**
 * Shared memory-management helpers for persisted turn events.
 *
 * Text fields (speech, reasoning, etc.) are kept intact in the store — long
 * thought blocks are collapsed at render time via CollapsibleMarkdown. Tool
 * `result` strings are still pruned here because they can be huge JSON blobs
 * and callers like extractSourcesFromThoughts run on the raw pre-prune list.
 */

export const MAX_CHAT_MESSAGES = 40;
export const MAX_MESSAGES_WITH_THOUGHTS = 5;
export const MAX_THINKING_EVENTS = 100;
export const MAX_TOOL_RESULT_LENGTH = 2500;

/** Collapsed preview length for thought/reasoning blocks in the UI. */
export const THOUGHT_TEXT_PREVIEW_CHARS = 1500;

const RESULT_TRUNCATION_SUFFIX = '\n... (result truncated for memory optimization)';

export type PrunableEvent = {
    type: string;
    text?: string;
    result?: string;
};

/**
 * Prune bulky fields before persisting events to chat history.
 * Speech and thought text are never truncated — use render-time collapse instead.
 */
export function pruneEvent<T extends PrunableEvent>(event: T): T {
    const pruned = { ...event };

    if (
        pruned.result
        && typeof pruned.result === 'string'
        && pruned.result.length > MAX_TOOL_RESULT_LENGTH
    ) {
        pruned.result = `${pruned.result.substring(0, MAX_TOOL_RESULT_LENGTH)}${RESULT_TRUNCATION_SUFFIX}`;
    }

    return pruned;
}

export function truncateForPreview(text: string, maxChars: number): string {
    if (text.length <= maxChars) return text;
    return `${text.slice(0, maxChars)}…`;
}
