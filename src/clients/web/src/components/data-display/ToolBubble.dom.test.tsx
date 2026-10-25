// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import ToolBubble from './ToolBubble';

afterEach(cleanup);

it('keeps running details collapsed until requested and preserves the choice on completion', async () => {
    const event = { type: 'tool_start' as const, name: 'web_search', params: { query: 'fictional example' }, timestamp: 1 };
    const { rerender, container } = render(<ToolBubble event={event} status="pending" />);
    const header = screen.getByRole('button');
    expect(header.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('Inputs')).toBeNull();
    fireEvent.keyDown(header, { key: 'Enter' });
    expect(header.getAttribute('aria-expanded')).toBe('true');
    rerender(<ToolBubble event={{ ...event, result: '{"count":2,"items":["A","B"]}' }} status="success" />);
    await screen.findByText('Result');
    expect(screen.getByRole('button', { name: /: tool details$/ }).getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelectorAll('pre')[1]?.textContent).toBe('{\n  "count": 2,\n  "items": [\n    "A",\n    "B"\n  ]\n}');
});
