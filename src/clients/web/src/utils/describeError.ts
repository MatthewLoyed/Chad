/**
 * Turn a thrown value into something a person can act on.
 *
 * `CommandError` messages read like `Command 'ListWorkflowsCommand' failed:
 * Failed to fetch` — accurate, and useless to the user. Map the cases we
 * actually produce to plain language and keep the technical detail in the
 * console: standard error-state guidance is that raw jargon ("Failed to
 * fetch", "HTTP 500") should never reach the user.
 *
 * Lives outside AsyncState.tsx so that file only exports components, which is
 * what react-refresh needs for fast refresh to work.
 */
export function describeError(err: unknown): string {
    const raw = err instanceof Error ? err.message : String(err ?? '');
    if (/failed to fetch|networkerror|load failed/i.test(raw)) {
        return "Chad isn't reachable right now. Check that the Brain is running, then try again.";
    }
    if (/timed out|timeout/i.test(raw)) {
        return 'Chad took too long to respond. It may be busy with another task.';
    }
    const status = raw.match(/HTTP (\d{3})/);
    if (status) {
        if (status[1] === '401' || status[1] === '403') {
            return 'Chad refused the request. Your access token may be out of date.';
        }
        return `Chad returned an error (${status[1]}).`;
    }
    if (/unavailable on this client/i.test(raw)) {
        return raw;
    }
    return 'Something went wrong loading this.';
}
