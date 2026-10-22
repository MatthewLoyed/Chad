import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    conversationMatches,
    guardConversation,
    getSessionViolations,
    resetSessionViolations,
} from './sessionGuard';

beforeEach(() => {
    resetSessionViolations();
    // Silence the intentional console.error a violation emits; we assert on the
    // recorded violations, not the console, and don't want red noise in test output.
    vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('conversationMatches', () => {
    it('matches identical ids', () => {
        expect(conversationMatches('conv-a', 'conv-a')).toBe(true);
    });

    it('rejects different ids', () => {
        expect(conversationMatches('conv-a', 'conv-b')).toBe(false);
    });

    it('treats a null/undefined incoming id as legacy → match (escape hatch)', () => {
        expect(conversationMatches(null, 'conv-a')).toBe(true);
        expect(conversationMatches(undefined, 'conv-a')).toBe(true);
    });

    it('a null active only matches a null incoming', () => {
        expect(conversationMatches(null, null)).toBe(true);
        expect(conversationMatches('conv-a', null)).toBe(false);
    });
});

describe('guardConversation — the choke-point', () => {
    it('records nothing on a match', () => {
        expect(guardConversation('conv-a', 'conv-a', 'unit')).toBe(true);
        expect(getSessionViolations()).toHaveLength(0);
    });

    it('records a violation on a mismatch and returns false', () => {
        expect(guardConversation('conv-a', 'conv-b', 'unit')).toBe(false);
        const violations = getSessionViolations();
        expect(violations).toHaveLength(1);
        expect(violations[0]).toMatchObject({ context: 'unit', incoming: 'conv-a', active: 'conv-b' });
    });

    it('logs loudly so a leak is visible in devtools the instant it happens', () => {
        guardConversation('conv-a', 'conv-b', 'unit');
        expect(console.error).toHaveBeenCalledOnce();
    });
});
