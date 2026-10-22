/**
 * Accent theming. The whole UI keys its highlight color off the CSS variable
 * `--chad-purple-rgb` (an "R, G, B" triplet). Changing that one variable retints
 * rails, buttons, pills, and the active states everywhere — so a user-picked
 * accent is a one-line personalization with no per-component work.
 */

export interface AccentPreset {
    id: string;
    label: string;
    rgb: string; // "r, g, b"
}

export const ACCENT_PRESETS: AccentPreset[] = [
    { id: 'violet', label: 'Violet (default)', rgb: '139, 92, 246' },
    { id: 'indigo', label: 'Indigo', rgb: '99, 102, 241' },
    { id: 'cyan', label: 'Cyan', rgb: '0, 200, 235' },
    { id: 'emerald', label: 'Emerald', rgb: '16, 185, 129' },
    { id: 'amber', label: 'Amber', rgb: '245, 158, 11' },
    { id: 'rose', label: 'Rose', rgb: '244, 63, 94' },
];

const STORAGE_KEY = 'chad-accent';
const DEFAULT_ID = 'violet';

export function getStoredAccent(): string {
    try {
        return localStorage.getItem(STORAGE_KEY) || DEFAULT_ID;
    } catch {
        return DEFAULT_ID;
    }
}

/** Apply an accent preset to the document root and persist the choice. */
export function applyAccent(id: string): void {
    const preset = ACCENT_PRESETS.find((p) => p.id === id) ?? ACCENT_PRESETS[0];
    document.documentElement.style.setProperty('--chad-purple-rgb', preset.rgb);
    try {
        localStorage.setItem(STORAGE_KEY, preset.id);
    } catch {
        /* storage unavailable — accent still applies for this session */
    }
}

/** Apply the stored accent at boot (call once on app start). */
export function initAccent(): void {
    applyAccent(getStoredAccent());
}
