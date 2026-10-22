// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ChatInput from './ChatInput';
import SkillsCatalog from '../settings/SkillsCatalog';
import { useAppStore } from '../../store';

vi.mock('./ModelSelector', () => ({ default: () => null }));
vi.mock('../data-display/TokenUsageTicker', () => ({ default: () => null }));
vi.mock('../modals', () => ({ DriveBrowserModal: () => null }));
vi.mock('../../transport/brainSocket', () => ({ brainSocket: { sendUiTurnMode: vi.fn() } }));

const skills = [{ name: 'microsoft-assignment', description: 'Read Canvas and prepare assignment files', enabled: true }];

beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ apiReady: true, status: 'idle', fastTrackCommands: [], activeModel: null, chatTurnMode: 'chat' });
    window.pywebview = { api: {
        get_skills_catalog: vi.fn().mockResolvedValue(skills),
        get_skill_content: vi.fn().mockResolvedValue({ success: true, content: 'Read Canvas and open working files.' }),
    } } as unknown as typeof window.pywebview;
    vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

it('discovers slash skills and sends the assignment argument without expanding it in the UI', async () => {
    const send = vi.fn();
    render(<ChatInput onSend={send} />);
    const input = screen.getByPlaceholderText('Ask Chad...');
    fireEvent.change(input, { target: { value: '/mic' } });
    fireEvent.click(await screen.findByRole('option', { name: /microsoft-assignment/ }));
    expect((input as HTMLTextAreaElement).value).toBe('/microsoft-assignment ');
    expect(send).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '/microsoft-assignment 5' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(send).toHaveBeenCalledOnce();
    expect(send.mock.calls[0][0]).toBe('/microsoft-assignment 5');
});

it('supports arrow and tab selection and keeps skill commands separate from tool shortcuts', async () => {
    useAppStore.setState({ fastTrackCommands: [{ alias: 'microsoft-assignment', label: 'Old macro', tool: 'run_routine' }] as never });
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText('Ask Chad...');
    fireEvent.change(input, { target: { value: '/mic' } });
    await screen.findByRole('option', { name: /Read Canvas/ });
    expect(screen.queryByText('Old macro')).toBeNull();
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Tab' });
    expect((input as HTMLTextAreaElement).value).toBe('/microsoft-assignment ');
});

it('lets the skill catalog insert a command without executing it', async () => {
    const useSkill = vi.fn();
    render(<SkillsCatalog onUseSkill={useSkill} />);
    fireEvent.click(await screen.findByRole('button', { name: /microsoft-assignment/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use /microsoft-assignment in chat' }));
    expect(useSkill).toHaveBeenCalledWith('microsoft-assignment');
});
