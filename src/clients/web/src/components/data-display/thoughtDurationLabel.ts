export function formatThoughtDurationLabel(durationMs?: number | null): string {
    if (durationMs == null || !Number.isFinite(durationMs) || durationMs <= 0) {
        return 'Thought';
    }
    if (durationMs < 500) {
        return 'Thought briefly';
    }
    const secs = Math.max(1, Math.round(durationMs / 1000));
    return secs === 1 ? 'Thought for 1 second' : `Thought for ${secs} seconds`;
}
