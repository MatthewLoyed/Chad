import { useEffect, useState } from 'react';
import {
    WAIT_THEATER_FALLBACK,
    WAIT_THEATER_TICK_MS,
    isLiveStatusStale,
} from '../utils/waitTheater';

/**
 * Resolves the label shown in the active ThinkingWidget header.
 * Prefers a fresh backend `liveStatus`; when stale, rotates client fallback phrases.
 */
export function useWaitTheaterStatus(
    liveStatus: string | null | undefined,
    liveStatusUpdatedAt: number | null | undefined,
    isActive: boolean,
): string | null {
    const [carouselIdx, setCarouselIdx] = useState(0);

    // Rewind the fallback rotation when the turn ends, so the next turn starts at the
    // first phrase. Done during render rather than in the effect below: the effect owns
    // the interval, and mixing a synchronous reset into it commits an extra render.
    const [wasActive, setWasActive] = useState(isActive);
    if (wasActive !== isActive) {
        setWasActive(isActive);
        if (!isActive) setCarouselIdx(0);
    }

    useEffect(() => {
        if (!isActive) {
            return;
        }
        const id = window.setInterval(() => {
            if (isLiveStatusStale(liveStatus, liveStatusUpdatedAt)) {
                setCarouselIdx((i) => (i + 1) % WAIT_THEATER_FALLBACK.length);
            }
        }, WAIT_THEATER_TICK_MS);
        return () => window.clearInterval(id);
    }, [isActive, liveStatus, liveStatusUpdatedAt]);

    if (!isActive) return null;

    if (liveStatus && !isLiveStatusStale(liveStatus, liveStatusUpdatedAt)) {
        return liveStatus;
    }

    return WAIT_THEATER_FALLBACK[carouselIdx];
}
