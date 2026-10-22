import { describe, it, expect } from 'vitest';
import { describeError } from './describeError';

describe('describeError', () => {
    it('translates a failed fetch into reachability language, not "Failed to fetch"', () => {
        const err = new Error("Command 'ListWorkflowsCommand' failed: Failed to fetch");
        const out = describeError(err);
        expect(out).toMatch(/isn't reachable/i);
        expect(out).not.toMatch(/failed to fetch/i);
        expect(out).not.toMatch(/ListWorkflowsCommand/);
    });

    it('distinguishes a timeout from an unreachable Brain', () => {
        expect(describeError(new Error("Command 'X' timed out"))).toMatch(/too long/i);
    });

    it('reports an HTTP status without leaking the raw body', () => {
        const out = describeError(new Error('HTTP 500 from Brain: Traceback (most recent call last)'));
        expect(out).toContain('500');
        expect(out).not.toMatch(/Traceback/);
    });

    it('calls out an auth failure specifically', () => {
        expect(describeError(new Error('HTTP 401 from Brain: '))).toMatch(/token/i);
    });

    it('never returns raw exception text for an unrecognised error', () => {
        const out = describeError(new Error('TypeError: cannot read property nope of undefined'));
        expect(out).toBe('Something went wrong loading this.');
    });
});
