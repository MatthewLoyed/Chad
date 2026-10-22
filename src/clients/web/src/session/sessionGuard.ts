/**
 * Session isolation guard (issue #269).
 * =====================================
 *
 * THE isolation contract for the web Limb. Live turn state — the in-flight
 * stream, a finalized bubble, and especially an interactive approval /
 * clarifying-question card — belongs to exactly ONE conversation. Every place
 * that would apply such state to the visible chat MUST route the decision
 * through this module so there is a single choke-point where a stray,
 * mis-attributed event is rejected.
 *
 * Why a dedicated module instead of an inline `if`:
 *  - One definition of "does this event belong to the chat on screen?" — no
 *    subtly-different comparisons drifting apart across transport/store.
 *  - Rejections are RECORDED, not silent. `getSessionViolations()` lets tests
 *    assert "a correct flow produces zero violations" as a standing invariant,
 *    so a future refactor that reintroduces cross-session bleed fails CI
 *    instantly instead of surfacing as a mystery popup in production.
 *  - In the browser a violation is a loud `console.error`, so if one ever slips
 *    past tests it is obvious in the devtools console the moment it happens.
 *
 * See docs/architecture/SESSION_ISOLATION.md for the full contract.
 */

export interface SessionViolation {
    /** Where the mismatch was caught (e.g. "TOOL_APPROVAL_REQUEST", "attach-approval"). */
    context: string;
    /** Conversation the event claimed to belong to. */
    incoming: string | null | undefined;
    /** Conversation the user is actually looking at. */
    active: string | null | undefined;
    /** epoch ms */
    at: number;
}

const violations: SessionViolation[] = [];

export const LIVE_BUSY_STATUSES = ['thinking', 'speaking', 'executing', 'model_loading'] as const;

export function isBusyStatus(status: string | null | undefined): boolean {
    return status != null && LIVE_BUSY_STATUSES.includes(status as (typeof LIVE_BUSY_STATUSES)[number]);
}

function recordViolation(
    incoming: string | null | undefined,
    active: string | null | undefined,
    context: string,
): void {
    const violation: SessionViolation = { context, incoming, active, at: Date.now() };
    violations.push(violation);
    console.error(
        `[session-isolation] Dropped "${context}": event for conversation ` +
            `${incoming ?? 'null'} does not match active ${active ?? 'null'}`,
    );
}

/**
 * True when an event tagged for `incoming` may be applied to the chat showing
 * `active`. A `null`/`undefined` `incoming` means "not session-tagged" — a
 * legacy emitter that predates conversation stamping. We treat that as a match
 * so old paths don't regress, but every emitter should stamp a real id so this
 * escape hatch trends to zero. A `null` `active` (splash / no chat open) only
 * matches a `null` incoming.
 */
export function conversationMatches(
    incoming: string | null | undefined,
    active: string | null | undefined,
): boolean {
    if (incoming == null) return true;
    return incoming === active;
}

/**
 * Strict live-state ownership: a running turn / busy status may only influence
 * the visible chat when it has a concrete owner and that owner is the active
 * conversation. Unlike {@link conversationMatches}, untagged live state is not
 * accepted because it can light up controls in an unrelated session.
 */
export function ownsActiveConversation(
    owner: string | null | undefined,
    active: string | null | undefined,
): boolean {
    if (owner == null || active == null) return false;
    return conversationMatches(owner, active);
}

export function guardActiveConversation(
    owner: string | null | undefined,
    active: string | null | undefined,
    context: string,
): boolean {
    if (ownsActiveConversation(owner, active)) return true;
    recordViolation(owner, active, context);
    return false;
}

/**
 * Guarded predicate: same truth as {@link conversationMatches}, but a mismatch
 * is recorded (and logged) so it can be surfaced by tests and devtools. Callers
 * MUST honour the boolean — a `false` means "drop this event, it is not for the
 * active conversation".
 */
export function guardConversation(
    incoming: string | null | undefined,
    active: string | null | undefined,
    context: string,
): boolean {
    if (conversationMatches(incoming, active)) return true;
    recordViolation(incoming, active, context);
    return false;
}

/** All violations recorded so far (newest last). Tests read this to assert isolation. */
export function getSessionViolations(): readonly SessionViolation[] {
    return violations;
}

/** Clear the recorded violations — call in test setup/teardown. */
export function resetSessionViolations(): void {
    violations.length = 0;
}
