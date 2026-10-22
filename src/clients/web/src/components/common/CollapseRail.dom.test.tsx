// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import CollapseRail from './CollapseRail';

afterEach(cleanup);

describe('CollapseRail', () => {
    it('calls onCollapse when the rail is clicked', () => {
        const onCollapse = vi.fn();
        render(
            <CollapseRail onCollapse={onCollapse} label="Collapse section">
                <p>Body</p>
            </CollapseRail>
        );

        fireEvent.click(screen.getByRole('button', { name: 'Collapse section' }));
        expect(onCollapse).toHaveBeenCalledTimes(1);
    });

    it('hides the hit target when inactive', () => {
        const onCollapse = vi.fn();
        const { container } = render(
            <CollapseRail active={false} onCollapse={onCollapse} label="Collapse section">
                <p>Body</p>
            </CollapseRail>
        );

        const hit = container.querySelector('.collapse-rail__hit') as HTMLButtonElement;
        expect(hit.tabIndex).toBe(-1);
        expect(hit.getAttribute('aria-hidden')).toBe('true');
        expect(container.querySelector('.collapse-rail--inactive')).toBeTruthy();
    });
});
