/** Scout's report must survive both live structured results and persisted JSON text. */
export function extractScoutReport(name: string | undefined, result: unknown): string | undefined {
    if (name !== 'scout_gather' && name !== 'dispatch_scout') return undefined;
    let value = result;
    for (let i = 0; i < 4; i++) {
        if (typeof value === 'string') {
            const text = value;
            try { value = JSON.parse(text); } catch { return text.trim() || undefined; }
        } else if (value && typeof value === 'object') {
            const data = value as Record<string, unknown>;
            if (typeof data.gather_brief === 'string' && data.gather_brief.trim()) return data.gather_brief;
            value = data.result ?? data.error;
        } else return undefined;
    }
    return typeof value === 'string' ? value : undefined;
}
