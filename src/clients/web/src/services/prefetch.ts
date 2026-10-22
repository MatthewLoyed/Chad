import { writeCache } from '../utils/localCache';

/**
 * Background prefetch service for workspaces and settings.
 * Called on startup after apiReady is confirmed, so data is cached and ready
 * before the user ever clicks on workspace tabs or opens settings.
 */
export async function prefetchWorkspaces(): Promise<void> {
    const api = window.pywebview?.api;
    if (!api) return;

    await Promise.allSettled([
        // 1. Automations summary & cron jobs
        (async () => {
            if (typeof api.get_automations_summary === 'function') {
                const summary = await api.get_automations_summary();
                if (summary) writeCache('automations_summary', summary);
            }
        })(),
        (async () => {
            if (typeof api.list_cron_jobs === 'function') {
                const jobs = await api.list_cron_jobs();
                if (Array.isArray(jobs)) writeCache('automations_cron_jobs', jobs);
            }
        })(),
        // 2. Routines & Tool catalog
        (async () => {
            const getter = api.get_routines ?? api.get_workflows;
            if (typeof getter === 'function') {
                const routines = await getter();
                if (Array.isArray(routines)) writeCache('routines', routines);
            }
        })(),
        (async () => {
            if (typeof api.list_tool_catalog === 'function') {
                const catalog = await api.list_tool_catalog();
                if (Array.isArray(catalog)) writeCache('tool_catalog', catalog);
            }
        })(),
        // 3. Kanban cards
        (async () => {
            if (typeof api.get_kanban_cards === 'function') {
                const cards = await api.get_kanban_cards();
                if (Array.isArray(cards)) writeCache('kanban_cards', cards);
            }
        })(),
        // 4. Dashboard feed
        (async () => {
            if (typeof api.get_dashboard_feed === 'function') {
                const feed = await api.get_dashboard_feed(50);
                if (Array.isArray(feed)) writeCache('dashboard_feed', feed);
            }
        })(),
        // 5. Settings: Integrations (MCP Servers & Native Connectors)
        (async () => {
            if (typeof api.get_mcp_servers === 'function') {
                const servers = await api.get_mcp_servers();
                if (Array.isArray(servers)) writeCache('mcp_servers', servers);
            }
        })(),
        (async () => {
            if (typeof api.get_native_connectors === 'function') {
                const connectors = await api.get_native_connectors();
                if (Array.isArray(connectors)) writeCache('native_connectors', connectors);
            }
        })(),
        // 6. Settings: Services Health & Search Quota / Backend
        (async () => {
            if (typeof api.get_services_health === 'function') {
                const r = await api.get_services_health() as { services?: unknown[] };
                if (Array.isArray(r?.services)) writeCache('services_health', r.services);
            }
        })(),
        (async () => {
            if (typeof api.get_search_quota === 'function') {
                const quota = await api.get_search_quota();
                if (quota) writeCache('search_quota', quota);
            }
        })(),
        (async () => {
            if (typeof api.get_searxng_settings === 'function') {
                const s = await api.get_searxng_settings() as { search_backend?: string };
                if (s?.search_backend) writeCache('searxng_backend', s.search_backend);
            }
        })(),
        // 7. Settings: System
        (async () => {
            if (typeof api.get_system_settings === 'function') {
                const sys = await api.get_system_settings() as { version?: unknown; auto_update?: boolean };
                if (sys?.version) writeCache('system_version', sys.version);
                if (typeof sys?.auto_update === 'boolean') writeCache('system_auto_update', sys.auto_update);
            }
        })(),
        // 8. Settings: Presenton, Chad Code, Browser Safety, Subagents, Skills
        (async () => {
            if (typeof api.get_presenton_settings === 'function') {
                const p = await api.get_presenton_settings();
                if (p) writeCache('presenton_settings', p);
            }
        })(),
        (async () => {
            if (typeof api.get_chad_code_settings === 'function') {
                const c = await api.get_chad_code_settings();
                if (c) writeCache('chad_code_settings', c);
            }
        })(),
        (async () => {
            if (typeof api.get_browser_safety_settings === 'function') {
                const b = await api.get_browser_safety_settings();
                if (b) writeCache('browser_safety_settings', b);
            }
        })(),
        (async () => {
            if (typeof api.get_subagents === 'function') {
                const subs = await api.get_subagents();
                if (Array.isArray(subs)) writeCache('subagents_list', subs);
            }
        })(),
        (async () => {
            if (typeof api.get_skills_catalog === 'function') {
                const skills = await api.get_skills_catalog();
                if (Array.isArray(skills)) writeCache('skills_catalog', skills);
            }
        })(),
        // 9. Models & Audio
        (async () => {
            if (typeof api.get_available_models === 'function') {
                const models = await api.get_available_models();
                if (Array.isArray(models)) writeCache('cloud_models', models);
            }
        })(),
        (async () => {
            if (typeof api.discover_local_models === 'function') {
                const local = await api.discover_local_models();
                if (Array.isArray(local)) {
                    writeCache('local_models', local);
                    const opts = local.map((m: Record<string, unknown>) => {
                        const id = String(m.name ?? m.id ?? '');
                        return id ? { id, name: id.split('/').pop()?.replace(/-/g, ' ') ?? id } : null;
                    }).filter(Boolean);
                    writeCache('presenton_local_models', opts);
                    writeCache('chad_code_local_models', opts);
                    writeCache('subagents_local_models', local);
                }
            }
        })(),
        (async () => {
            if (typeof api.get_audio_devices === 'function') {
                const devs = await api.get_audio_devices();
                if (Array.isArray(devs)) {
                    const normalized = devs.map((d: Record<string, unknown>) => ({
                        id: String(d.id ?? d.index ?? ''),
                        name: String(d.name ?? 'Unknown device'),
                        active: Boolean(d.active),
                    })).filter((d) => d.id !== '');
                    writeCache('audio_devices', normalized);
                }
            }
        })(),
    ]);
}
