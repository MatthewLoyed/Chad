/**
 * Parity guard: every `window.pywebview.api.*` method the UI calls must either
 * exist on the browser shim or be a deliberate desktop-only exception.
 *
 * This exists because the Automations workspace and the Capabilities settings
 * tab were permanently stuck on their loading spinner in the browser/PWA
 * client — not because the Brain was unreachable, but because the shim never
 * implemented `get_automations_summary`, and both panes bail out on a missing
 * method BEFORE clearing `loading`. Nothing failed loudly; a whole nav
 * destination was just dead on the phone.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(__dirname, '..');
const SHIM = path.join(SRC, 'transport', 'pywebviewShim.ts');

/**
 * Methods the shim intentionally does NOT provide, with the reason. Adding a
 * name here is a claim that the UI degrades gracefully without it.
 */
const DESKTOP_ONLY: Record<string, string> = {
    assess_model_hardware: 'desktop-only local hardware; never upload to a remote Brain',
    start_model_setup_job: 'explicit local setup operations; no browser execution surface',
    get_model_setup_job: 'desktop setup job progress',
    cancel_model_setup_job: 'desktop setup job cancellation',
    get_setup_status: 'first-run wizard is desktop-only (App.tsx guards on isBrowserTransport)',
    probe_local_backend: 'first-run wizard is desktop-only',
    save_provider_choice: 'first-run wizard is desktop-only',
    start_lmstudio_setup: 'first-run wizard is desktop-only',
    get_lmstudio_setup_progress: 'first-run wizard is desktop-only',
    hide_hud: 'HUD window is a pywebview window',
    download_artifact: 'intentionally absent — artifactFiles.ts falls back to an anchor download (#528)',
    set_ui_turn_mode: 'browser path sends the mode over the WS instead (ChatInput.tsx)',
    get_wake_word_mode: 'wake word / mic pipeline is desktop-only; SettingsView hides the section when absent',
    set_wake_word_mode: 'wake word / mic pipeline is desktop-only; SettingsView hides the section when absent',
    get_pairing_info: 'MUST stay desktop-only — it returns the server token. Deliberately has no HTTP route so nothing on the network can request it, and PairPhoneCard renders null under isBrowserTransport (the browser client IS the paired device; it already holds the token and must never re-display it).',
    trigger_ingestion_sync: 'reads Chrome profile from local disk; desktop-only',
};

function walk(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(p, out);
        else if (/\.tsx?$/.test(entry.name) && !/\.test\./.test(entry.name) && p !== SHIM) out.push(p);
    }
    return out;
}

function shimMethods(): Set<string> {
    const src = fs.readFileSync(SHIM, 'utf8');
    const body = src.slice(src.indexOf('const api: Record<string, ApiFn> = {'));
    return new Set([...body.matchAll(/^ {8}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]));
}

function apiMethodsUsedByUi(): Map<string, string[]> {
    const used = new Map<string, string[]>();
    for (const file of walk(SRC)) {
        const src = fs.readFileSync(file, 'utf8');
        for (const m of src.matchAll(/\bapi\??\.([a-z_][a-z0-9_]*)\s*[()]/g)) {
            const list = used.get(m[1]) ?? [];
            list.push(path.relative(SRC, file));
            used.set(m[1], list);
        }
    }
    return used;
}

describe('browser shim / UI parity', () => {
    it('implements every api method the UI calls, except documented desktop-only ones', () => {
        const shim = shimMethods();
        const missing: string[] = [];
        for (const [method, files] of apiMethodsUsedByUi()) {
            if (shim.has(method) || method in DESKTOP_ONLY) continue;
            missing.push(`${method} (called from ${[...new Set(files)].join(', ')})`);
        }
        expect(missing).toEqual([]);
    });

    it('keeps the automations surface wired — the panes hang without it', () => {
        const shim = shimMethods();
        for (const method of [
            'get_automations_summary',
            'list_cron_jobs',
            'create_cron_job',
            'set_cron_job_status',
            'delete_cron_job',
            'update_daemon_settings',
            'update_heartbeat_checklist',
            'get_native_connectors',
            'enable_native_connector',
            'delete_native_connector',
        ]) {
            expect(shim.has(method), `${method} missing from pywebviewShim`).toBe(true);
        }
    });

    it('does not silently grow the desktop-only exception list', () => {
        // Each entry is a pane that degrades rather than works on web/PWA.
        // Raised 8 -> 10 for the wake-word pair (issue #654): the mic and wake
        // word engine physically live in the desktop Limb, so there is nothing
        // for the browser client to call.
        // Raised 10 -> 11 for `get_pairing_info`, which is a different KIND of
        // exception from the rest: not "this pane degrades on the phone" but
        // "this must never be reachable from the phone". It hands back the server
        // Raised 11 -> 12 for `trigger_ingestion_sync` (reads local Chrome profile).
        // Issue #5 adds four intentionally local-only hardware/download job
        // methods. Exposing them over the network would scan the wrong host
        // and turn a desktop installer into a remote execution surface.
        expect(Object.keys(DESKTOP_ONLY).length).toBeLessThanOrEqual(16);
    });
});
