import { describe, it, expect } from 'vitest';
import { isLiveStatusStale, WAIT_THEATER_STALE_MS } from './waitTheater';

describe('waitTheater', () => {
    it('treats missing status as stale', () => {
        expect(isLiveStatusStale(null, Date.now())).toBe(true);
        expect(isLiveStatusStale('', Date.now())).toBe(true);
    });

    it('treats fresh backend status as not stale', () => {
        const now = 1_000_000;
        expect(isLiveStatusStale('Selecting tools…', now - 500, now)).toBe(false);
    });

    it('treats status older than STALE_MS as stale', () => {
        const now = 1_000_000;
        expect(isLiveStatusStale('Selecting tools…', now - WAIT_THEATER_STALE_MS - 1, now)).toBe(true);
    });
});
