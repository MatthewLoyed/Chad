// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import SubagentPanel from './SubagentPanel';
import { extractScoutReport } from '../../utils/scout-report';
import { reduceStreamEvents, partsToThinkingEvents } from '../../protocol/streamReducer';
import type { StreamEvent } from '../../protocol/streamEvents';

afterEach(cleanup);

it('retains Scout header, child attribution, reasoning and report through live reduction', () => {
    const events = [
        {type: 'run_started'},
        {type: 'tool_start', tool_call_id: 'scout-dispatch', name: 'dispatch_scout', args: {task: 'Gather app facts'}},
        {type: 'reasoning_delta', part_id: 'reason', delta: 'Checking the available tools.', parent_id: 'scout-dispatch', agent_name: 'Scout'},
        {type: 'tool_start', tool_call_id: 'lookup', name: 'search_for_tool', parent_id: 'scout-dispatch'},
        {type: 'tool_end', tool_call_id: 'lookup', ok: true, result: 'Found a tool'},
        {type: 'tool_end', tool_call_id: 'scout-dispatch', ok: true, result: {result: 'Verified app facts', gather_brief: 'Verified app facts'}},
    ].map((e, seq) => ({...e, seq, turn_id: 'turn'} as StreamEvent));
    const adapted = partsToThinkingEvents(reduceStreamEvents(events));
    const header = adapted.find(e => e.correlation_id === 'scout-dispatch')!;
    expect(header.parent_id).toBeUndefined();
    const children = adapted.filter(e => e.parent_id === header.correlation_id);
    expect(children.some(e => e.name === 'search_for_tool')).toBe(true);
    expect(children.some(e => e.text === 'Checking the available tools.')).toBe(true);
    render(<SubagentPanel agentName="Scout" prompt={header.prompt} status="success" report={extractScoutReport(header.name, header.result)}>
        {children.map((e, i) => <div key={i}>{e.text || e.name}</div>)}
    </SubagentPanel>);
    fireEvent.click(screen.getByRole('button', {name: /Scout/}));
    expect(screen.getByText('Verified app facts')).toBeDefined();
    expect(screen.getByText('Checking the available tools.')).toBeDefined();
    expect(screen.getByText('search_for_tool')).toBeDefined();
});

it('supports legacy nested JSON reports without showing an object dump', () => {
    expect(extractScoutReport('scout_gather', JSON.stringify({result: {gather_brief: 'Scout report'}}))).toBe('Scout report');
    expect(extractScoutReport('open_app', 'not Scout')).toBeUndefined();
});
