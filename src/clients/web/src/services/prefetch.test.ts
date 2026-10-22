// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { prefetchWorkspaces } from './prefetch';
import { readCache } from '../utils/localCache';

describe('prefetchWorkspaces', () => {
    beforeEach(() => {
        localStorage.clear();
        delete window.pywebview;
    });

    it('no-ops safely when window.pywebview is missing', async () => {
        await expect(prefetchWorkspaces()).resolves.toBeUndefined();
    });

    it('populates cache for all workspaces and settings when API methods resolve', async () => {
        window.pywebview = {
            api: {
                get_automations_summary: vi.fn().mockResolvedValue({
                    heartbeat: { status: 'online' },
                    daemons: [{ id: 'heartbeat', label: 'Heartbeat' }],
                }),
                list_cron_jobs: vi.fn().mockResolvedValue([
                    { task_id: 'task-1', name: 'Morning digest' },
                ]),
                get_routines: vi.fn().mockResolvedValue([
                    { id: 'routine-1', name: 'Standup' },
                ]),
                list_tool_catalog: vi.fn().mockResolvedValue([
                    { name: 'web_search', description: 'Search the web' },
                ]),
                get_kanban_cards: vi.fn().mockResolvedValue([
                    { id: 'card-1', title: 'Ship v2' },
                ]),
                get_dashboard_feed: vi.fn().mockResolvedValue([
                    { id: 'feed-1', title: 'Morning thought' },
                ]),
                get_mcp_servers: vi.fn().mockResolvedValue([
                    { id: 'memory', name: 'Memory', connected: true },
                ]),
                get_native_connectors: vi.fn().mockResolvedValue([
                    { id: 'linear', name: 'Linear', connected: true },
                ]),
                get_services_health: vi.fn().mockResolvedValue({
                    services: [{ id: 'searxng', label: 'SearXNG', healthy: true }],
                }),
                get_search_quota: vi.fn().mockResolvedValue({
                    tavily: { configured: true, available: true, remaining: 50 },
                }),
                get_searxng_settings: vi.fn().mockResolvedValue({
                    search_backend: 'all',
                }),
                get_system_settings: vi.fn().mockResolvedValue({
                    version: { sha: 'abcdef' },
                    auto_update: true,
                }),
                get_presenton_settings: vi.fn().mockResolvedValue({
                    enabled: true,
                    url: 'http://localhost:5000',
                }),
                get_chad_code_settings: vi.fn().mockResolvedValue({
                    coder_model: 'qwen',
                }),
                get_browser_safety_settings: vi.fn().mockResolvedValue({
                    approval_mode: 'model_decide',
                    allowlist: ['google.com'],
                }),
                get_subagents: vi.fn().mockResolvedValue([
                    { name: 'code_agent', enabled: true },
                ]),
                get_skills_catalog: vi.fn().mockResolvedValue([
                    { name: 'python-guidelines', description: 'Rules for python' },
                ]),
                get_available_models: vi.fn().mockResolvedValue([
                    { id: 'gemini-pro', name: 'Gemini Pro' },
                ]),
                discover_local_models: vi.fn().mockResolvedValue([
                    { name: 'qwen/32b', provider: 'lmstudio' },
                ]),
                get_audio_devices: vi.fn().mockResolvedValue([
                    { id: 'mic-1', name: 'Built-in Mic', active: true },
                ]),
            },
        };

        await prefetchWorkspaces();

        expect(readCache('automations_summary')).toEqual({
            heartbeat: { status: 'online' },
            daemons: [{ id: 'heartbeat', label: 'Heartbeat' }],
        });
        expect(readCache('automations_cron_jobs')).toEqual([
            { task_id: 'task-1', name: 'Morning digest' },
        ]);
        expect(readCache('routines')).toEqual([
            { id: 'routine-1', name: 'Standup' },
        ]);
        expect(readCache('tool_catalog')).toEqual([
            { name: 'web_search', description: 'Search the web' },
        ]);
        expect(readCache('kanban_cards')).toEqual([
            { id: 'card-1', title: 'Ship v2' },
        ]);
        expect(readCache('dashboard_feed')).toEqual([
            { id: 'feed-1', title: 'Morning thought' },
        ]);
        expect(readCache('mcp_servers')).toEqual([
            { id: 'memory', name: 'Memory', connected: true },
        ]);
        expect(readCache('native_connectors')).toEqual([
            { id: 'linear', name: 'Linear', connected: true },
        ]);
        expect(readCache('services_health')).toEqual([
            { id: 'searxng', label: 'SearXNG', healthy: true },
        ]);
        expect(readCache('search_quota')).toEqual({
            tavily: { configured: true, available: true, remaining: 50 },
        });
        expect(readCache('searxng_backend')).toBe('all');
        expect(readCache('system_version')).toEqual({ sha: 'abcdef' });
        expect(readCache('system_auto_update')).toBe(true);
        expect(readCache('presenton_settings')).toEqual({
            enabled: true,
            url: 'http://localhost:5000',
        });
        expect(readCache('chad_code_settings')).toEqual({
            coder_model: 'qwen',
        });
        expect(readCache('browser_safety_settings')).toEqual({
            approval_mode: 'model_decide',
            allowlist: ['google.com'],
        });
        expect(readCache('subagents_list')).toEqual([
            { name: 'code_agent', enabled: true },
        ]);
        expect(readCache('skills_catalog')).toEqual([
            { name: 'python-guidelines', description: 'Rules for python' },
        ]);
        expect(readCache('cloud_models')).toEqual([
            { id: 'gemini-pro', name: 'Gemini Pro' },
        ]);
        expect(readCache('local_models')).toEqual([
            { name: 'qwen/32b', provider: 'lmstudio' },
        ]);
        expect(readCache('audio_devices')).toEqual([
            { id: 'mic-1', name: 'Built-in Mic', active: true },
        ]);
    });

    it('continues prefetching remaining tabs if one method throws', async () => {
        window.pywebview = {
            api: {
                get_automations_summary: vi.fn().mockRejectedValue(new Error('Network error')),
                list_cron_jobs: vi.fn().mockResolvedValue([
                    { task_id: 'task-1', name: 'Backup' },
                ]),
                get_kanban_cards: vi.fn().mockResolvedValue([
                    { id: 'card-2', title: 'Bug fix' },
                ]),
            },
        };

        await prefetchWorkspaces();

        expect(readCache('automations_summary')).toBeNull();
        expect(readCache('automations_cron_jobs')).toEqual([
            { task_id: 'task-1', name: 'Backup' },
        ]);
        expect(readCache('kanban_cards')).toEqual([
            { id: 'card-2', title: 'Bug fix' },
        ]);
    });
});
