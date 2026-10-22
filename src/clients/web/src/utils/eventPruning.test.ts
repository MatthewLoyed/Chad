import { describe, expect, it } from 'vitest';
import { pruneEvent, truncateForPreview } from './eventPruning';

describe('pruneEvent', () => {
    it('does not truncate speech text', () => {
        const long = 'x'.repeat(10_000);
        const event = { type: 'speech' as const, text: long };
        expect(pruneEvent(event).text).toBe(long);
    });

    it('does not truncate reasoning text', () => {
        const long = 'y'.repeat(10_000);
        const event = { type: 'reasoning' as const, text: long };
        expect(pruneEvent(event).text).toBe(long);
    });

    it('still truncates oversized tool result strings', () => {
        const long = 'z'.repeat(3000);
        const event = { type: 'tool_start' as const, result: long };
        const pruned = pruneEvent(event);
        expect(pruned.result!.length).toBeLessThan(long.length);
        expect(pruned.result).toContain('result truncated for memory optimization');
    });
});

describe('truncateForPreview', () => {
    it('returns the original string when within the limit', () => {
        expect(truncateForPreview('short', 100)).toBe('short');
    });

    it('appends an ellipsis when over the limit', () => {
        expect(truncateForPreview('abcdef', 3)).toBe('abc…');
    });
});
