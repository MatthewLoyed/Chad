const PREFIX = 'chad:cache:';

/** Synchronously read a cached value for `key`, or null if absent/corrupt. */
export function readCache<T>(key: string): T | null {
    try {
        const raw = localStorage.getItem(PREFIX + key);
        return raw ? (JSON.parse(raw) as T) : null;
    } catch {
        return null;
    }
}

/** Persist `value` for `key`. Silently no-ops if storage is unavailable (e.g. quota). */
export function writeCache<T>(key: string, value: T): void {
    try {
        localStorage.setItem(PREFIX + key, JSON.stringify(value));
    } catch {
        // ignore
    }
}
