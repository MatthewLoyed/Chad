/**
 * Client-side wait-theater carousel (Phase A).
 * Rotates fallback phase labels when the backend hasn't sent a fresh status
 * within STALE_MS — keeps the pre-first-token gap visually alive.
 */

export const WAIT_THEATER_STALE_MS = 2500;
export const WAIT_THEATER_TICK_MS = 2500;

export const WAIT_THEATER_OPENERS = [
    'Got it — working on this…',
    'On it…',
    'One moment…',
    'Reading your message…',
] as const;

/** Shown when backend status is stale; cycled client-side. */
export const WAIT_THEATER_FALLBACK = [
    'Still working…',
    'Pulling things together…',
    'Getting context ready…',
    'Almost there…',
    'Hang tight…',
    'Processing…',
    'Working through it…',
] as const;

export function pickWaitTheaterOpener(): string {
    const i = Math.floor(Math.random() * WAIT_THEATER_OPENERS.length);
    return WAIT_THEATER_OPENERS[i];
}

export function isLiveStatusStale(
    liveStatus: string | null | undefined,
    liveStatusUpdatedAt: number | null | undefined,
    now = Date.now(),
): boolean {
    if (!liveStatus) return true;
    if (!liveStatusUpdatedAt) return true;
    return now - liveStatusUpdatedAt > WAIT_THEATER_STALE_MS;
}
