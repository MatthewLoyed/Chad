/**
 * Browser pywebview shim (#188).
 * ==============================
 *
 * Reconstructs the `window.pywebview.api.*` surface the React client calls — the
 * desktop GUI gets this object injected by pywebview; a browser doesn't, so we
 * build an equivalent on top of `commandClient` (REST) and `brainSocket` (WS).
 *
 * Method → backend mapping mirrors the desktop API mixins
 * (src/clients/desktop/api/*.py): each `_execute_command(XCommand(...))` becomes
 * `executeCommand('XCommand', {...})`; the WS sends (send_text_command,
 * invoke_fast_track, run_workflow, resolve_tool_approval) go through brainSocket.
 *
 * Methods that on desktop read local process state (ConfigManager, the webview
 * window, OS dialogs, the microphone) have no browser equivalent; they return
 * graceful defaults so the UI degrades instead of throwing. The streaming +
 * history + settings paths #188 targets are fully wired.
 */
import { executeCommand } from './commandClient';
import { brainSocket } from './brainSocket';
import { getServerUrl, getToken, toHttpBase } from './transportConfig';
import { useAppStore } from '../store';

type ApiFn = (...args: unknown[]) => Promise<unknown>;

const ok = <T>(v: T): Promise<T> => Promise.resolve(v);

function buildApi(): Record<string, ApiFn> {
    const api: Record<string, ApiFn> = {
        // ── Chat history ──
        get_conversations: (limit = 50, offset = 0) =>
            executeCommand('GetConversationsCommand', { limit: Number(limit), offset: Number(offset) }),
        get_chat_messages: (conversation_id) =>
            executeCommand('GetMessagesCommand', { conversation_id }),
        delete_chat: (conversation_id) =>
            executeCommand('DeleteConversationCommand', { conversation_id }),
        rename_chat: (conversation_id, title) =>
            executeCommand('UpdateConversationTitleCommand', { conversation_id, title }),
        pin_chat: (conversation_id, is_pinned = true) =>
            executeCommand('PinConversationCommand', { conversation_id, is_pinned: !!is_pinned }),
        move_chat_to_project: (conversation_id, project_path = null) =>
            executeCommand('SetConversationProjectCommand', { conversation_id, project_path: project_path || null }),
        get_workspace_projects: () => executeCommand('ListWorkspaceProjectsCommand', {}),
        create_workspace_project: (name) => executeCommand('CreateWorkspaceProjectCommand', { name }),
        rename_workspace_project: (old_path, new_name) =>
            executeCommand('RenameWorkspaceProjectCommand', { old_path, new_name }),
        create_new_chat: (model = null, title = 'New Chat', domain = null) =>
            executeCommand('CreateConversationCommand', { model, title, domain: domain || null }),
        search_chats: (query) => executeCommand('SearchChatsCommand', { query }),

        // ── Domain agents (Categories) ──
        list_domains: () => executeCommand('ListDomainsCommand', {}),
        create_domain: (name, description = '', icon = '', color = '', persona = '') =>
            executeCommand('CreateDomainCommand', { name, description, icon, color, persona }),
        update_domain: (domain_id, fields) =>
            executeCommand('UpdateDomainCommand', { domain_id, fields }),
        delete_domain: (domain_id) => executeCommand('DeleteDomainCommand', { domain_id }),
        move_chat_to_domain: (conversation_id, domain = null) =>
            executeCommand('SetConversationDomainCommand', { conversation_id, domain: domain || null }),

        // ── Turn dispatch (WebSocket) ──
        // `metadata` is accepted for desktop-signature parity but unused on the WS path.
        send_text_command: (
            text,
            image_base64 = null,
            content_blocks = null,
            conversation_id = null,
            metadata = null,
            category = null,
            thinking_enabled = null,
            domain = null,
            retry = null,
            keep_count = null,
        ) =>
            (void metadata, brainSocket.sendInput(
                String(text ?? ''),
                content_blocks as unknown[] | null,
                conversation_id as string | null,
                category as string | null,
                thinking_enabled as boolean | null,
                image_base64 as string | null,
                domain as string | null,
                retry as boolean | null,
                keep_count as number | null,
            )),
        invoke_fast_track: (alias) => ok(brainSocket.sendFastTrackInvoke(String(alias))),
        run_workflow: (workflow_id) => ok(brainSocket.sendWorkflowRun(String(workflow_id))),
        resolve_tool_approval: (token, decision, displayText) =>
            brainSocket.resolveToolApproval(String(token), String(decision), displayText ? String(displayText) : undefined),
        stop_active_task: () => {
            brainSocket.abortActiveTurn();
            return executeCommand('StopActiveTaskCommand', {}).then(() => true).catch(() => false);
        },
        reconnect_server: () => {
            // Immediate UI feedback before the socket finishes opening.
            try {
                const { setStatus, setStatusText } = useAppStore.getState();
                setStatus('reconnecting');
                setStatusText('Trying to reconnect...');
            } catch { /* store may not be ready */ }
            brainSocket.reconnect();
            return ok(true);
        },

        // ── Routines (saved tool sequences) ──
        get_workflows: () => executeCommand('ListWorkflowsCommand', {}),
        get_routines: () => executeCommand('ListWorkflowsCommand', {}),
        list_tool_catalog: () => executeCommand('ListToolCatalogCommand', {}),
        draft_routine: (description) =>
            executeCommand('DraftRoutineCommand', { description: String(description ?? '') }),
        get_skills_catalog: () => executeCommand('ListSkillsCatalogCommand', {}),
        get_skill_content: (skill_name) =>
            executeCommand('GetSkillContentCommand', { skill_name: String(skill_name) }),
        get_tool_selector_model: () => executeCommand('GetToolSelectorModelCommand', {}),
        set_tool_selector_model: (model_id, provider) =>
            executeCommand('SetToolSelectorModelCommand', { model_id, provider }),
        get_routine_builder_model: () => executeCommand('GetRoutineBuilderModelCommand', {}),
        set_routine_builder_model: (model_id) =>
            executeCommand('SetRoutineBuilderModelCommand', { model_id: String(model_id) }),
        create_workflow: (name, steps, description = null) =>
            executeCommand('CreateWorkflowCommand', { name: String(name).trim(), steps: steps || [], description })
                .then((workflow) => ({ success: workflow != null, workflow })),
        update_workflow: (workflow_id, name = null, description = null, steps = null) =>
            executeCommand('UpdateWorkflowCommand', { workflow_id, name, description, steps })
                .then((success) => ({ success })),
        delete_workflow: (workflow_id) =>
            executeCommand('DeleteWorkflowCommand', { workflow_id }).then((success) => ({ success })),

        // ── Kanban ──
        get_kanban_cards: () => executeCommand('ListKanbanCardsCommand', {}),
        create_kanban_card: (title, description = null, column_id = 'todo', order_index = 0) =>
            executeCommand('CreateKanbanCardCommand', { title: String(title).trim(), description, column_id, order_index })
                .then((card) => ({ success: card != null, card })),
        update_kanban_card: (card_id, title = null, description = null) =>
            executeCommand('UpdateKanbanCardCommand', { card_id, title, description }).then((success) => ({ success })),
        move_kanban_card: (card_id, column_id, order_index = 0) =>
            executeCommand('MoveKanbanCardCommand', { card_id, column_id, order_index }).then((success) => ({ success })),
        delete_kanban_card: (card_id) =>
            executeCommand('DeleteKanbanCardCommand', { card_id }).then((success) => ({ success })),

        // ── Memory ──
        get_memories: () => executeCommand('GetMemoriesCommand', { limit: 250 }),
        get_memory_catalog: () => executeCommand('GetMemoryCatalogCommand', { preview_limit: 6 }),
        get_memory_section: (section_id, offset = 0, limit = 25) =>
            executeCommand('GetMemorySectionCommand', {
                section_id: String(section_id),
                offset: Number(offset),
                limit: Number(limit),
            }),
        search_memories: (query) => query ? executeCommand('SearchMemoriesCommand', { query, limit: 100 }) : ok([]),
        add_memory: (content, category = 'general') =>
            executeCommand<number>('SaveMemoryCommand', { content, category })
                .then((id) => ({ success: id > 0, id })),
        update_memory: (memory_id, content) =>
            executeCommand('UpdateMemoryCommand', { memory_id: Number(memory_id), content }).then((success) => ({ success })),
        delete_memory: (memory_id) =>
            executeCommand('DeleteMemoryCommand', { memory_id: Number(memory_id) }).then((success) => ({ success })),
        purge_all_memories: () => executeCommand('ClearMemoriesCommand', { memory_type: 'all' }),
        get_memory_stats: () => executeCommand('GetStorageStatsCommand', {}),
        semantic_search_memories: (query) =>
            query ? executeCommand('SemanticSearchMemoriesCommand', { query, limit: 10 }) : ok([]),

        // ── System / dashboard / capability ──
        get_capability_suggestions: (limit = 5) =>
            executeCommand('GetCapabilitySuggestionsCommand', { limit: Number(limit) }),
        set_capability_suggestion_status: (suggestion_id, status) =>
            executeCommand('SetCapabilitySuggestionStatusCommand', { suggestion_id, status }),
        propose_capability_skill_build: (suggestion_id) =>
            executeCommand('ProposeCapabilitySkillBuildCommand', { suggestion_id }),
        commit_capability_skill_build: (suggestion_id, approval_token) =>
            executeCommand('CommitCapabilitySkillBuildCommand', { suggestion_id, approval_token }),
        get_reflection_lessons: (limit = 20) =>
            executeCommand('GetReflectionLessonsCommand', { limit: Number(limit) }),
        set_reflection_lesson_status: (lesson_id, status) =>
            executeCommand('SetReflectionLessonStatusCommand', { lesson_id, status }),
        answer_reflection_question: (lesson_id, answer) =>
            executeCommand('AnswerReflectionQuestionCommand', { lesson_id, answer }),
        get_dashboard_feed: (limit = 50) => executeCommand('GetDashboardFeedCommand', { limit: Number(limit) }),
        run_dashboard_action: (entry_id) => executeCommand('RunDashboardActionCommand', { entry_id }),
        dismiss_dashboard_entry: (entry_id) => executeCommand('DismissDashboardEntryCommand', { entry_id }),
        get_context_status: () => executeCommand('GetContextStatusCommand', {}),
        force_consolidation: (conversation_id = 'default') =>
            executeCommand('ForceConsolidationCommand', { conversation_id }),

        // ── Automations: heartbeat / cron / webhooks / daemons ──
        // Absent until #670-follow-up: AutomationsTab and CapabilitiesTab both bail out
        // via `if (!api?.get_automations_summary) return;` BEFORE clearing their loading
        // flag, so on the browser/PWA client those panes sat on "Loading automations…"
        // forever — even with a healthy Brain. Mirrors src/clients/desktop/api/automations.py.
        get_automations_summary: () => executeCommand('GetAutomationsSummaryCommand', {}),
        list_cron_jobs: () => executeCommand('ListCronJobsCommand', {}).catch(() => []),
        create_cron_job: (name, description, schedule, trigger = 'cron', is_agentic = false) =>
            executeCommand('CreateCronJobCommand', {
                name: String(name ?? ''),
                description: String(description ?? ''),
                schedule: String(schedule ?? ''),
                trigger: String(trigger ?? 'cron'),
                is_agentic: !!is_agentic,
            }),
        set_cron_job_status: (task_id, status) =>
            executeCommand('SetCronJobStatusCommand', { task_id, status }),
        delete_cron_job: (task_id) => executeCommand('DeleteCronJobCommand', { task_id }),
        update_heartbeat_checklist: (content) =>
            executeCommand('UpdateHeartbeatChecklistCommand', { content: String(content ?? '') }),
        get_webhook_status: () => executeCommand('GetWebhookStatusCommand', {}).catch(() => ({})),
        list_autonomous_runs: (limit = 50, source = null) =>
            executeCommand('ListAutonomousRunsCommand', { limit: Number(limit), source: source ?? null })
                .catch(() => []),
        update_daemon_settings: (daemon_id, enabled = null, interval_minutes = null) =>
            executeCommand('UpdateDaemonSettingsCommand', {
                daemon_id,
                enabled: enabled === null || enabled === undefined ? null : !!enabled,
                interval_minutes:
                    interval_minutes === null || interval_minutes === undefined
                        ? null
                        : Number(interval_minutes),
            }),

        // ── Native connectors (Gmail / Telegram / Canvas / stock images) ──
        get_native_connectors: () => executeCommand('GetNativeConnectorsCommand', {}).catch(() => []),
        enable_native_connector: (connector_id) =>
            executeCommand('EnableNativeConnectorCommand', { connector_id }),
        delete_native_connector: (connector_id) =>
            executeCommand('DeleteNativeConnectorCommand', { connector_id }),
        save_telegram_connector: (bot_token) =>
            executeCommand('SaveTelegramConnectorCommand', { bot_token: String(bot_token ?? '') }),
        save_canvas_connector: (base_url, token, contact_email = '') =>
            executeCommand('SaveCanvasConnectorCommand', {
                base_url: String(base_url ?? ''),
                token: String(token ?? ''),
                contact_email: String(contact_email ?? ''),
            }),
        save_stock_images_connector: (pexels_key = '', unsplash_key = '', pixabay_key = '') =>
            executeCommand('SaveStockImagesConnectorCommand', {
                pexels_key: String(pexels_key ?? ''),
                unsplash_key: String(unsplash_key ?? ''),
                pixabay_key: String(pixabay_key ?? ''),
            }),
        save_finance_connector: (client_id = '', secret = '', env = 'sandbox') =>
            executeCommand('SaveFinanceConnectorCommand', {
                client_id: String(client_id ?? ''),
                secret: String(secret ?? ''),
                env: String(env ?? 'sandbox'),
            }),
        create_finance_link_token: (user_id = 'chad-user') =>
            executeCommand('CreateFinanceLinkTokenCommand', { user_id }),
        exchange_finance_token: (public_token, metadata = null) =>
            executeCommand('ExchangeFinanceTokenCommand', { public_token, metadata }),
        list_finance_institutions: () =>
            executeCommand('ListFinanceInstitutionsCommand', {}),
        disconnect_finance_institution: (item_id) =>
            executeCommand('DisconnectFinanceInstitutionCommand', { item_id }),

        // ── Config: models / MCP / subagents ──
        set_voice: (voice_name) => executeCommand('SetVoiceCommand', { voice_name }),
        set_model: (model_name) => executeCommand('SetModelCommand', { model_name }),
        discover_local_models: () => executeCommand('DiscoverLocalModelsCommand', {}),
        set_active_model: (model_info) => {
            const mi = typeof model_info === 'string' ? JSON.parse(model_info) : model_info;
            return executeCommand<boolean | { success: boolean }>('SetActiveModelCommand', { model_info: mi })
                .then(result => typeof result === 'boolean' ? result : result?.success === true);
        },
        get_mcp_servers: () => executeCommand('GetMCPServersCommand', {}).catch(() => []),
        add_mcp_server: (name, command, env, extra = null) =>
            executeCommand('AddMCPServerCommand', { name, command, env, extra: extra || {} }),
        update_mcp_server: (name, command, env, extra = null) =>
            executeCommand('AddMCPServerCommand', { name, command, env, extra: extra || {} }),
        delete_mcp_server: (name) => executeCommand('DeleteMCPServerCommand', { name }),
        toggle_mcp_server: (name, enabled) => executeCommand('ToggleMCPServerCommand', { name, enabled: !!enabled }),
        test_mcp_connection: (name, command, env, extra = null) =>
            executeCommand('TestMCPConnectionCommand', { name, command, env, extra: extra || {} }),
        get_subagents: () => executeCommand('GetSubagentsCommand', {}),
        toggle_subagent: (name, enabled) => executeCommand('ToggleSubagentCommand', { name, enabled: !!enabled }),
        set_subagent_model: (name, model_info) => {
            const mi = (model_info || {}) as Record<string, unknown>;
            return executeCommand('SetSubagentModelCommand', {
                name,
                model_name: mi.model_name ?? null,
                provider: mi.provider ?? null,
                thinking_level: mi.thinking_level ?? null,
            });
        },
        get_browser_safety_settings: () => executeCommand('GetBrowserSafetyCommand', {}),
        set_browser_safety_settings: (approval_mode, allowlist) =>
            executeCommand('SetBrowserSafetyCommand', {
                approval_mode: approval_mode ?? null,
                allowlist: allowlist ?? null,
            }),
        get_presenton_settings: () => executeCommand('GetPresentonSettingsCommand', {}),
        set_presenton_settings: (
            enabled,
            url,
            llm_url,
            model,
            use_async,
            restart_sidecar = false,
        ) =>
            executeCommand('SetPresentonSettingsCommand', {
                enabled: enabled ?? null,
                url: url ?? null,
                llm_url: llm_url ?? null,
                model: model ?? null,
                use_async: use_async ?? null,
                restart_sidecar: restart_sidecar === true,
            }),
        get_chad_code_settings: () => executeCommand('GetChadCodeSettingsCommand', {}),
        set_chad_code_settings: (
            coder_model,
            max_iterations,
            job_timeout_s,
            use_task_tracker,
            native_tool_calling,
            use_condenser,
            use_critic,
            context7_mcp,
            github_mcp,
            stream_events,
        ) =>
            executeCommand('SetChadCodeSettingsCommand', {
                coder_model: coder_model ?? null,
                max_iterations: max_iterations ?? null,
                job_timeout_s: job_timeout_s ?? null,
                use_task_tracker: use_task_tracker ?? null,
                native_tool_calling: native_tool_calling ?? null,
                use_condenser: use_condenser ?? null,
                use_critic: use_critic ?? null,
                context7_mcp: context7_mcp ?? null,
                github_mcp: github_mcp ?? null,
                stream_events: stream_events ?? null,
            }),
        chad_code_preflight: () => executeCommand('ChadCodePreflightCommand', {}),
        chad_code_job_status: (job_id: unknown) =>
            executeCommand('ChadCodeJobStatusCommand', { job_id: String(job_id ?? '') }),
        get_searxng_settings: () => executeCommand('GetSearxngSettingsCommand', {}),
        set_searxng_settings: (search_backend) =>
            executeCommand('SetSearxngSettingsCommand', { search_backend: search_backend ?? null }),
        get_search_quota: () => executeCommand('GetSearchQuotaCommand', {}),
        start_searxng_sidecar: () => executeCommand('StartSearxngSidecarCommand', {}),
        get_supabase_settings: () => executeCommand('GetSupabaseSettingsCommand', {}),
        bootstrap_supabase: (url, anon_key, service_key = '', db_url = '') =>
            executeCommand('BootstrapSupabaseCommand', {
                url: String(url ?? ''),
                anon_key: String(anon_key ?? ''),
                service_key: String(service_key ?? ''),
                db_url: String(db_url ?? ''),
            }),

        // ── Audio (headless Brain → mock; safe defaults) ──
        get_audio_devices: () => executeCommand('GetAudioDevicesCommand', {}).catch(() => []),
        set_audio_device: (index) => executeCommand('SetAudioDeviceCommand', { device_index: Number(index) }),
        switch_audio_device_by_id: (device_id) => executeCommand('SwitchOutputDeviceCommand', { device_id }),
        get_volume: () => executeCommand('GetVolumeCommand', {}).catch(() => 1.0),
        set_volume: (volume) => executeCommand('SetVolumeCommand', { volume: Number(volume) }),
        get_audio_level: () => ok(0),
        test_microphone: () => ok({ success: false, heard: false, message: 'Microphone test requires the desktop app.' }),
        test_wake_word: () => ok({ success: false, detected: false, message: 'Wake word test requires the desktop app.' }),
        get_autonomous_announcements: () => ok(true),
        set_autonomous_announcements: (enabled) => ok({ success: true, enabled: Boolean(enabled) }),

        // ── Google Workspace ──
        list_drive_files: (query = null, limit = 50) =>
            executeCommand('ListDriveFilesCommand', { query, limit }),
        get_drive_file_content: (file_id, mime_type) =>
            executeCommand('GetDriveFileContentCommand', { file_id, mime_type }),
        check_google_auth: () => executeCommand('CheckGoogleAuthCommand', {}),
        start_google_auth: () => executeCommand('StartGoogleAuthCommand', {}),

        // ── "Update Chad" / dev branch switch (authenticated REST endpoint) ──
        update_chad: async (...args: unknown[]) => {
            const branch = typeof args[0] === 'string' ? args[0] : undefined;
            const base = toHttpBase(getServerUrl());
            const token = getToken();
            const payload: Record<string, unknown> = { force: true };
            if (branch) payload.branch = branch;
            try {
                const resp = await fetch(`${base}/api/update`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...(token ? { 'x-chad-token': token } : {}),
                    },
                    body: JSON.stringify(payload),
                });
                const data = await resp.json();
                if (data.restarting) brainSocket.reconnect();
                return data;
            } catch (e) {
                return { success: false, error: e instanceof Error ? e.message : String(e) };
            }
        },

        get_branches: async () => {
            const base = toHttpBase(getServerUrl());
            const token = getToken();
            try {
                const resp = await fetch(`${base}/api/branches`, {
                    headers: token ? { 'x-chad-token': token } : {},
                });
                return await resp.json();
            } catch (e) {
                return { success: false, error: e instanceof Error ? e.message : String(e) };
            }
        },

        // ── System settings: version + auto-update (authenticated REST) ──
        get_system_settings: async () => {
            const base = toHttpBase(getServerUrl());
            const token = getToken();
            try {
                const resp = await fetch(`${base}/api/system/settings`, {
                    headers: token ? { 'x-chad-token': token } : {},
                });
                return await resp.json();
            } catch (e) {
                return { success: false, error: e instanceof Error ? e.message : String(e) };
            }
        },
        // ── Services health: Brain/LM Studio/SearXNG/Kroki/Typst/Marp (issue #371) ──
        get_services_health: async () => {
            const base = toHttpBase(getServerUrl());
            const token = getToken();
            try {
                const resp = await fetch(`${base}/api/services/health`, {
                    headers: token ? { 'x-chad-token': token } : {},
                });
                return await resp.json();
            } catch (e) {
                return { success: false, error: e instanceof Error ? e.message : String(e) };
            }
        },
        set_auto_update: async (enabled, interval) => {
            const base = toHttpBase(getServerUrl());
            const token = getToken();
            const body: Record<string, unknown> = { auto_update: !!enabled };
            if (typeof interval === 'number') body.auto_update_interval_minutes = interval;
            try {
                const resp = await fetch(`${base}/api/system/settings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', ...(token ? { 'x-chad-token': token } : {}) },
                    body: JSON.stringify(body),
                });
                return await resp.json();
            } catch (e) {
                return { success: false, error: e instanceof Error ? e.message : String(e) };
            }
        },

        // ── Local-process methods with browser equivalents or graceful no-ops ──
        get_server_url: () => ok(toHttpBase(getServerUrl())),
        get_status: () => ok(brainSocket.connected ? 'ready' : 'initializing'),
        get_raw_state: () => ok(brainSocket.connected ? 'ready' : 'initializing'),
        get_active_model_info: () =>
            ok({ mode: 'cloud', name: 'Chad', provider: 'cloud', supports_tools: true, thinking_enabled: null, model_settings: {} }),
        get_available_models: () => ok([]),
        get_model: () => ok(''),
        get_voice: () => executeCommand('GetVoiceNameCommand', {}).catch(() => 'Kore'),
        get_ui_preferences: () => ok({}),
        save_ui_preference: () => ok(false),
        update_model_settings: () => ok(false),
        get_voice_mode_state: () => ok(false),
        toggle_voice_mode: () => ok(false),
        start_dictation: () => ok({ success: false, message: 'Dictation requires the desktop app.' }),
        stop_dictation: () => ok({ success: false, text: '', message: 'Dictation requires the desktop app.' }),
        poll_dialog_queue: () => ok(undefined),
        confirm_tool_limit_extension: () => ok(false),
        confirm_file_plan_approval: () => ok(false),
        open_external_url: (url) => { if (typeof url === 'string') window.open(url, '_blank', 'noopener'); return ok(true); },
        open_local_file: () => ok(false),
        download_local_file: () => ok(false),
        // No download_artifact stub here (unlike the other no-ops above): artifactFiles.ts's
        // downloadArtifact() only takes the pywebview.api.download_artifact path when that
        // method actually exists, falling back to a real working anchor-click download
        // otherwise. A stub here — even one that reports failure — makes that check true and
        // silently swallows every browser-client download (issue #528).
        pick_file: () => ok({ success: false, error: 'File picking is unavailable in the browser client' }),
    };
    return api;
}

let installed = false;
let browserTransport = false;

/** True once the browser shim has been installed (i.e. not running inside the desktop GUI). */
export function isBrowserTransport(): boolean {
    return browserTransport;
}

/** Desktop identity is known before pywebview asynchronously injects its API. */
export function isDesktopPage(): boolean {
    return window.location.protocol === 'file:' ||
        new URLSearchParams(window.location.search).get('chadDesktop') === '1';
}

const LOOPBACK_HOSTNAMES = new Set(['localhost', '127.0.0.1', '::1']);

/**
 * True when this page was loaded from the same machine the Brain runs on.
 * Mirrors the backend's loopback trust (`src/remote/auth.py::is_loopback_host`):
 * identity can't be meaningfully verified for a same-machine caller anyway, so
 * the login gate only matters for non-loopback (Tailscale/remote) access.
 */
export function isLocalOrigin(): boolean {
    try {
        return LOOPBACK_HOSTNAMES.has(window.location.hostname);
    } catch {
        return false;
    }
}

/**
 * Install the browser transport — only when NOT running inside pywebview (the
 * desktop GUI injects its own `window.pywebview`, which we must never clobber).
 * Idempotent. Opens the Brain WebSocket if credentials are present.
 */
export function installBrowserTransport(): void {
    if (installed) return;
    if (isDesktopPage() || window.pywebview) return; // never replace a late native bridge
    installed = true;
    browserTransport = true;

    window.pywebview = { api: buildApi() };

    if (getToken()) {
        brainSocket.connect();
    }
}
