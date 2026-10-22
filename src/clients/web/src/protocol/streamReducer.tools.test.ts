import { describe, expect, it } from 'vitest';
import { applyStreamEvent, initialTurnState, partsToThinkingEvents } from './streamReducer';

describe('streamReducer — tools_snapshot', () => {
    it('folds tools_snapshot into a tool_menu part and thinking event', () => {
        let state = initialTurnState();
        state = applyStreamEvent(state, { type: 'run_started', turn_id: 't1', seq: 0 });
        state = applyStreamEvent(state, {
            type: 'tools_snapshot',
            turn_id: 't1',
            seq: 1,
            menu_id: 't1-tools',
            tools: ['web_search', 'read_file'],
            shortlist_size: 10,
            selection_path: 'hybrid',
        });

        expect(state.parts).toHaveLength(1);
        expect(state.parts[0]).toMatchObject({
            kind: 'tool_menu',
            tools: ['web_search', 'read_file'],
            shortlist_size: 10,
            selection_path: 'hybrid',
        });

        const events = partsToThinkingEvents(state);
        expect(events[0]).toMatchObject({
            type: 'tool_menu',
            tools: ['web_search', 'read_file'],
            shortlist_size: 10,
            selection_path: 'hybrid',
        });
    });

    it('replaces an existing tool_menu snapshot for the same menu_id', () => {
        let state = initialTurnState();
        state = applyStreamEvent(state, { type: 'run_started', turn_id: 't1', seq: 0 });
        state = applyStreamEvent(state, {
            type: 'tools_snapshot',
            turn_id: 't1',
            seq: 1,
            menu_id: 't1-tools',
            tools: ['web_search'],
        });
        state = applyStreamEvent(state, {
            type: 'tools_snapshot',
            turn_id: 't1',
            seq: 2,
            menu_id: 't1-tools',
            tools: ['web_search', 'kanban_board'],
            shortlist_size: 8,
        });

        expect(state.parts).toHaveLength(1);
        expect(state.parts[0]).toMatchObject({
            kind: 'tool_menu',
            tools: ['web_search', 'kanban_board'],
            shortlist_size: 8,
        });
    });
});
