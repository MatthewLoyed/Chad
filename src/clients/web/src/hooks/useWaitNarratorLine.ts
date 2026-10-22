import { useEffect, useState } from 'react';

export const WAIT_NARRATOR_TICK_MS = 3200;

/**
 * Rotates query-tailored narrator lines in the wait-theater dropdown.
 * Returns null when inactive or no lines are available.
 */
export function useWaitNarratorLine(
    lines: string[] | null | undefined,
    isActive: boolean,
): string | null {
    const [idx, setIdx] = useState(0);

    // Restart the rotation when the line set changes. Adjusting during render (rather
    // than in an effect) is React's documented pattern for derived-from-props state: it
    // resolves before paint instead of committing an extra render, so the first frame
    // never shows the previous set's line at a stale index.
    const [prevLines, setPrevLines] = useState(lines);
    if (lines !== prevLines) {
        setPrevLines(lines);
        setIdx(0);
    }

    useEffect(() => {
        if (!isActive || !lines?.length) {
            return;
        }
        const id = window.setInterval(() => {
            setIdx((i) => (i + 1) % lines.length);
        }, WAIT_NARRATOR_TICK_MS);
        return () => window.clearInterval(id);
    }, [isActive, lines]);

    if (!isActive || !lines?.length) return null;
    return lines[idx % lines.length] ?? null;
}
