// @vitest-environment jsdom
/**
 * Regression: streaming thought markdown must not show literal '*' list markers
 * or a leading turn-start "..." once repaired for live preview.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import CollapsibleMarkdown from '../common/CollapsibleMarkdown';

afterEach(cleanup);

describe('CollapsibleMarkdown streaming repairs', () => {
    it('strips leading placeholder ellipsis before render', () => {
        const { container } = render(
            <CollapsibleMarkdown content="...Aura farmers from anime" forceExpanded />,
        );
        expect(container.textContent || '').toMatch(/^Aura farmers/);
        expect(container.textContent || '').not.toMatch(/^\.\.\./);
    });

    it('closes unclosed fences so list markers become real <li>s', () => {
        const unclosed = `Plan:
\`\`\`
*   *Theme:* Dark, high-contrast
*   *Layout:* Hero section`;
        const { container } = render(
            <CollapsibleMarkdown content={unclosed} forceExpanded />,
        );
        const text = container.textContent || '';
        expect(text).not.toContain('*Theme:*');
        expect(text).toContain('Theme:');
        expect(container.querySelectorAll('li').length).toBeGreaterThanOrEqual(2);
    });
});
