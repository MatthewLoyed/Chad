// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import ToolMenuCarousel, { CORE_TOOL_NAMES } from './ToolMenuCarousel';

describe('ToolMenuCarousel', () => {
    it('returns null when menu only contains core tools', () => {
        const { container } = render(
            <ToolMenuCarousel
                menu={{
                    tools: Array.from(CORE_TOOL_NAMES),
                    shortlistSize: 20,
                    selectionPath: 'hybrid',
                }}
                isActive={true}
            />
        );
        expect(container.firstChild).toBeNull();
    });

    it('renders only extra tools when menu contains both core and extra tools', () => {
        const { container } = render(
            <ToolMenuCarousel
                menu={{
                    tools: [
                        'web_search',
                        'ask_user_question',
                        'read_file',
                        'write_to_file',
                    ],
                    shortlistSize: 10,
                    selectionPath: 'hybrid',
                }}
                isActive={true}
            />
        );

        const carousel = container.querySelector('.tool-menu-carousel');
        expect(carousel).not.toBeNull();

        const chips = container.querySelectorAll('.tool-menu-chip');
        expect(chips.length).toBe(2);

        const labels = Array.from(container.querySelectorAll('.tool-menu-chip__label')).map(
            (el) => el.textContent
        );
        expect(labels).toContain('read file');
        expect(labels).toContain('write to file');
        expect(labels).not.toContain('web search');
        expect(labels).not.toContain('ask user question');

        const meta = container.querySelector('.tool-menu-carousel__meta');
        expect(meta?.textContent).toBe('Picked 2 from 10 candidates');
    });

    it('displays singular tool count when exactly one extra tool is ready', () => {
        const { container } = render(
            <ToolMenuCarousel
                menu={{
                    tools: ['web_search', 'run_command'],
                }}
                isActive={false}
            />
        );

        const meta = container.querySelector('.tool-menu-carousel__meta');
        expect(meta?.textContent).toBe('1 tool ready');
    });
});
