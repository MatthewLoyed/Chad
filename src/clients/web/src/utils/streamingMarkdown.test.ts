import { describe, expect, it } from 'vitest';
import {
    closeUnclosedCodeFences,
    repairStreamingMarkdown,
    stripLeadingPlaceholderEllipsis,
} from './streamingMarkdown';

describe('stripLeadingPlaceholderEllipsis', () => {
    it('strips turn-start placeholder glued to the first token', () => {
        expect(stripLeadingPlaceholderEllipsis('...Aura farmers')).toBe('Aura farmers');
    });

    it('does not touch mid-sentence or standalone ellipses', () => {
        expect(stripLeadingPlaceholderEllipsis('likely referencing...')).toBe('likely referencing...');
        expect(stripLeadingPlaceholderEllipsis('...\n\nNext paragraph')).toBe('...\n\nNext paragraph');
    });
});

describe('closeUnclosedCodeFences', () => {
    it('drops a dangling opener so list asterisks leave code mode', () => {
        const open = 'Intro\n```\n*   *Theme:* Dark\n*   *Layout:* Hero';
        expect(closeUnclosedCodeFences(open)).toBe(
            'Intro\n*   *Theme:* Dark\n*   *Layout:* Hero',
        );
    });

    it('leaves balanced fences alone', () => {
        const ok = 'Before\n```js\nconst x = 1\n```\nAfter';
        expect(closeUnclosedCodeFences(ok)).toBe(ok);
    });
});

describe('repairStreamingMarkdown', () => {
    it('strips placeholder ellipsis and neutralizes dangling fences', () => {
        const raw = '...Aura\n```\n*   *Theme:* Dark';
        expect(repairStreamingMarkdown(raw)).toBe('Aura\n*   *Theme:* Dark');
    });
});
