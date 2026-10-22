/**
 * Subagent attribution round-trip.
 * ================================
 *
 * `parent_id` (the delegation correlation id) is the grouping key that lets the UI
 * isolate a subagent's thoughts + tool calls into their own panel. It must survive
 * reducer fold and the partsToThinkingEvents adapter, alongside `agent_name`.
 */
import { describe, expect, it } from 'vitest';
import { reduceStreamEvents, partsToThinkingEvents } from './streamReducer';
import type { StreamEvent } from './streamEvents';

const TURN = 'turn-1';
let seq = 0;
const ev = (e: Omit<StreamEvent, 'turn_id' | 'seq'>): StreamEvent =>
    ({ ...e, turn_id: TURN, seq: seq++ } as StreamEvent);

describe('subagent attribution', () => {
    it('reasoning_delta carries parent_id + agent_name through reduce', () => {
        seq = 0;
        const state = reduceStreamEvents([
            ev({ type: 'run_started' }),
            ev({ type: 'reasoning_delta', part_id: 'r0', delta: 'main thought' }),
            ev({ type: 'tool_start', tool_call_id: 'deleg1', name: 'delegate_to_research_agent' }),
            ev({ type: 'reasoning_delta', part_id: 'r1', delta: 'sub thought', agent_name: 'Research Agent', parent_id: 'deleg1' }),
            ev({ type: 'tool_start', tool_call_id: 'sub-tool', name: 'search_web', parent_id: 'deleg1' }),
            ev({ type: 'run_finished', response: 'done' }),
        ]);

        const subReasoning = state.parts.find(
            (p) => p.kind === 'reasoning' && p.text === 'sub thought',
        );
        expect(subReasoning).toMatchObject({ agent_name: 'Research Agent', parent_id: 'deleg1' });

        const subTool = state.parts.find((p) => p.kind === 'tool' && p.id === 'sub-tool');
        expect(subTool).toMatchObject({ parent_id: 'deleg1' });
    });

    it('partsToThinkingEvents preserves parent_id on reasoning and tools', () => {
        seq = 0;
        const events = partsToThinkingEvents(
            reduceStreamEvents([
                ev({ type: 'run_started' }),
                ev({ type: 'reasoning_delta', part_id: 'r1', delta: 'sub thought', agent_name: 'Code Agent', parent_id: 'deleg1' }),
                ev({ type: 'tool_start', tool_call_id: 'sub-tool', name: 'read_file', parent_id: 'deleg1' }),
                ev({ type: 'run_finished', response: 'ok' }),
            ]),
        );

        const reasoning = events.find((e) => e.type === 'reasoning');
        expect(reasoning).toMatchObject({ parent_id: 'deleg1', agent_name: 'Code Agent' });

        const tool = events.find((e) => e.type === 'tool_start' && e.correlation_id === 'sub-tool');
        expect(tool).toMatchObject({ parent_id: 'deleg1' });
    });

    it('partsToThinkingEvents preserves subagent prompt from tool args', () => {
        seq = 0;
        const events = partsToThinkingEvents(
            reduceStreamEvents([
                ev({ type: 'run_started' }),
                ev({
                    type: 'tool_start',
                    tool_call_id: 'deleg_1',
                    name: 'delegate_to_research_agent',
                    args: { task: 'Find 5 wedding venues in Lansing' },
                }),
                ev({ type: 'run_finished', response: 'ok' }),
            ]),
        );

        const deleg = events.find((e) => e.correlation_id === 'deleg_1');
        expect(deleg?.prompt).toBe('Find 5 wedding venues in Lansing');
    });

    it('Scout → Planner handoff yields separate reasoning parts (no shared parent_id)', () => {
        seq = 0;
        const state = reduceStreamEvents([
            ev({ type: 'run_started' }),
            ev({ type: 'tool_start', tool_call_id: 'scout_1', name: 'scout_gather' }),
            ev({
                type: 'reasoning_delta',
                part_id: 'r0',
                delta: 'scout facts',
                agent_name: 'Scout',
                parent_id: 'scout_1',
            }),
            ev({ type: 'reasoning_end', part_id: 'r0' }),
            ev({ type: 'tool_end', tool_call_id: 'scout_1', ok: true }),
            ev({ type: 'reasoning_delta', part_id: 'r1', delta: 'planner brief' }),
            ev({ type: 'run_finished', response: 'ok' }),
        ]);

        const scout = state.parts.find((p) => p.kind === 'reasoning' && p.id === 'r0');
        const planner = state.parts.find((p) => p.kind === 'reasoning' && p.id === 'r1');
        expect(scout).toMatchObject({ parent_id: 'scout_1', agent_name: 'Scout', ended: true });
        expect(planner).toMatchObject({ text: 'planner brief', ended: true });
        expect(planner?.parent_id).toBeUndefined();

        const adapted = partsToThinkingEvents(state);
        const scoutEvt = adapted.find((e) => e.type === 'reasoning' && e.text === 'scout facts');
        const plannerEvt = adapted.find((e) => e.type === 'reasoning' && e.text === 'planner brief');
        expect(scoutEvt).toMatchObject({ parent_id: 'scout_1' });
        expect(plannerEvt?.parent_id).toBeUndefined();

        const scoutHeader = adapted.find((e) => e.type === 'tool_start' && e.correlation_id === 'scout_1');
        expect(scoutHeader).toMatchObject({ success: true });
    });
});
