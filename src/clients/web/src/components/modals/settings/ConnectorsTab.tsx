import { useState } from 'react';
import { CheckCircle2, ChevronRight, Loader2, XCircle, Unplug, Globe, Trash2 } from 'lucide-react';
import {
    SiGithub,
    SiBrave,
    SiTodoist,
    SiNotion,
    SiGoogledrive,
    SiGoogle,
    SiTelegram,
    SiGroupme,
    SiSlack,
    SiLinear,
    SiJira,
} from 'react-icons/si';
import type { IconType } from 'react-icons';

// ── Catalog ───────────────────────────────────────────────────────────────────

interface CredentialField {
    name: string;
    description: string;
    isSecret: boolean;
    placeholder: string;
}

interface ConnectorDef {
    id: string;
    name: string;
    description: string;
    unlocks: string[];
    command: string[];
    credentials: CredentialField[];
    kind?: 'mcp' | 'native';
    /** Browser OAuth on first connect (via mcp-remote); no API keys required. */
    oauth?: boolean;
    /** Passed through to MCP server config (connect_timeout_seconds, etc.). */
    extra?: Record<string, number>;
    Icon: IconType | null;
    iconBg: string;
    iconColor: string;
    comingSoon?: boolean;
}

const PlaceholderIcon = Globe;

function oauthIntroCopy(connector: ConnectorDef): string {
    if (connector.id === 'canva') {
        return (
            'A browser window will open to sign into Canva. Free accounts work for '
            + 'generate/edit/export; monthly AI allowance and Pro features (resize, premium '
            + 'elements) follow your Canva plan. You only need to authorize once.'
        );
    }
    if (connector.id === 'supabase') {
        return (
            'A browser window will open to sign into Supabase and choose which projects '
            + 'Chad can access. You only need to do this once.'
        );
    }
    return `A browser window will open to sign into ${connector.name}. You only need to authorize once.`;
}

const CONNECTOR_CATALOG: ConnectorDef[] = [
    {
        id: 'github',
        name: 'GitHub',
        description: 'Browse repos, review pull requests, search issues, and read file contents.',
        unlocks: ['Browse code & repos', 'Create & review PRs', 'Search issues'],
        command: ['npx', '-y', '@modelcontextprotocol/server-github'],
        credentials: [
            { name: 'GITHUB_PERSONAL_ACCESS_TOKEN', description: 'Personal Access Token with repo scope — github.com/settings/tokens', isSecret: true, placeholder: 'ghp_...' },
        ],
        Icon: SiGithub,
        iconBg: '#24292e',
        iconColor: '#ffffff',
    },
    {
        id: 'brave-search',
        name: 'Brave Search',
        description: 'Real-time web search for up-to-date information and news.',
        unlocks: ['Web search', 'News lookup', 'Research any topic'],
        command: ['npx', '-y', '@modelcontextprotocol/server-brave-search'],
        credentials: [
            { name: 'BRAVE_API_KEY', description: 'API key from brave.com/search/api', isSecret: true, placeholder: 'BSAa...' },
        ],
        Icon: SiBrave,
        iconBg: '#fb542b',
        iconColor: '#ffffff',
    },
    {
        id: 'todoist',
        name: 'Todoist',
        description: 'Create and manage tasks, projects, and deadlines.',
        unlocks: ['Add & complete tasks', 'List projects', 'Set priorities'],
        command: ['npx', '-y', '@ganiev/todoist-mcp-server'],
        credentials: [
            { name: 'TODOIST_API_TOKEN', description: 'API token from Todoist Settings → Integrations', isSecret: true, placeholder: 'Your token' },
        ],
        Icon: SiTodoist,
        iconBg: '#db4035',
        iconColor: '#ffffff',
    },
    {
        id: 'notion',
        name: 'Notion',
        description: 'Read and write Notion pages, databases, and blocks.',
        unlocks: ['Read & create pages', 'Query databases', 'Update blocks'],
        command: ['npx', '-y', '@suekou/mcp-notion-server'],
        credentials: [
            { name: 'NOTION_API_TOKEN', description: 'Integration token from notion.so/my-integrations', isSecret: true, placeholder: 'secret_...' },
        ],
        Icon: SiNotion,
        iconBg: '#ffffff',
        iconColor: '#000000',
    },
    {
        id: 'playwright',
        name: 'Playwright',
        description: 'Full browser automation — screenshots, forms, and web scraping.',
        unlocks: ['Take screenshots', 'Navigate websites', 'Fill & submit forms'],
        command: ['npx', '-y', '@playwright/mcp@latest'],
        credentials: [],
        Icon: null,
        iconBg: '#2ead33',
        iconColor: '#ffffff',
    },
    {
        id: 'fetch',
        name: 'Web Fetch',
        description: 'Fetch and parse text content from any URL on the internet.',
        unlocks: ['Read web pages', 'Extract text', 'Follow links'],
        command: ['npx', '-y', 'mcp-fetch-server'],
        credentials: [],
        Icon: null,
        iconBg: '#4f46e5',
        iconColor: '#ffffff',
    },
    {
        id: 'google-drive',
        name: 'Google Drive',
        description: 'List and access files stored in your Google Drive.',
        unlocks: ['List files & folders', 'Read documents', 'Export content'],
        command: ['npx', '-y', '@piotr-agier/google-drive-mcp'],
        credentials: [
            { name: 'GOOGLE_DRIVE_OAUTH_CREDENTIALS', description: 'Path to OAuth credentials JSON', isSecret: false, placeholder: 'C:\\path\\to\\credentials.json' },
            { name: 'GOOGLE_DRIVE_MCP_TOKEN_PATH', description: 'Path to OAuth token JSON file', isSecret: false, placeholder: 'C:\\path\\to\\token.json' },
        ],
        Icon: SiGoogledrive,
        iconBg: '#ffffff',
        iconColor: '#4285f4',
    },
    {
        id: 'excalidraw',
        name: 'Excalidraw',
        description: 'Live hand-drawn whiteboard diagrams on a persistent canvas.',
        unlocks: ['Sketch whiteboards', 'Iterate on diagrams', 'Export canvas images'],
        command: ['npx', '-y', 'mcp-excalidraw-server'],
        credentials: [],
        extra: { timeout_seconds: 30 },
        Icon: null,
        iconBg: '#6965db',
        iconColor: '#ffffff',
    },
    {
        id: 'canvas',
        kind: 'native',
        name: 'Canvas LMS',
        description: 'Assignments, courses, grades, and syllabus from your school Canvas instance.',
        unlocks: ['List courses', 'Upcoming assignments', 'Read syllabus & files', 'Dashboard to-dos'],
        command: [],
        credentials: [
            {
                name: 'CANVAS_BASE_URL',
                description: 'Your school Canvas URL (e.g. https://canvas.university.edu)',
                isSecret: false,
                placeholder: 'https://your-school.instructure.com',
            },
            {
                name: 'CANVAS_TOKEN',
                description: 'Access token from Canvas → Account → Settings → Approved Integrations',
                isSecret: true,
                placeholder: 'Paste token',
            },
            {
                name: 'CANVAS_CONTACT_EMAIL',
                description: 'Contact email for Canvas API User-Agent (optional)',
                isSecret: false,
                placeholder: 'user@example.com',
            },
        ],
        Icon: null,
        iconBg: '#E72429',
        iconColor: '#ffffff',
    },
    {
        id: 'stock-images',
        kind: 'native',
        name: 'Stock Images',
        description:
            'Optional Pexels / Unsplash / Pixabay keys for search_images. Openverse (CC art) needs no key — see Services.',
        unlocks: ['search_images stock providers', 'Local HTML site photos', 'Downloadable image assets'],
        command: [],
        credentials: [
            {
                name: 'PEXELS_API_KEY',
                description: 'Free key from pexels.com/api (optional if you set Unsplash or Pixabay)',
                isSecret: true,
                placeholder: 'Pexels API key',
            },
            {
                name: 'UNSPLASH_ACCESS_KEY',
                description: 'Access Key from unsplash.com/developers (optional)',
                isSecret: true,
                placeholder: 'Unsplash Access Key',
            },
            {
                name: 'PIXABAY_API_KEY',
                description: 'Free key from pixabay.com/api/docs (optional)',
                isSecret: true,
                placeholder: 'Pixabay API key',
            },
        ],
        Icon: null,
        iconBg: '#0ea5e9',
        iconColor: '#ffffff',
    },
    {
        id: 'gmail',
        kind: 'native',
        name: 'Gmail',
        description: 'Read, search, and send email via Google Workspace.',
        unlocks: ['Search inbox', 'Read messages', 'Send email', 'Push notifications (webhook)'],
        command: [],
        credentials: [],
        Icon: SiGoogle,
        iconBg: '#ffffff',
        iconColor: '#ea4335',
    },
    {
        id: 'outlook',
        kind: 'native',
        name: 'Outlook (School & Work)',
        description: 'Read and ingest school or enterprise emails via local Classic Outlook desktop profile (e.g. @msstate.edu).',
        unlocks: ['School email context in memory', 'Search class announcements & billing', 'Bypasses university IT admin consent walls'],
        command: [],
        credentials: [],
        Icon: null,
        iconBg: '#0078d4',
        iconColor: '#ffffff',
    },
    {
        id: 'telegram',
        kind: 'native',
        name: 'Telegram',
        description: 'Chat with Chad from your phone and receive proactive notifications.',
        unlocks: ['Mobile chat', 'Push notifications', 'Autonomous alerts'],
        command: [],
        credentials: [
            { name: 'TELEGRAM_BOT_TOKEN', description: 'Bot token from @BotFather on Telegram', isSecret: true, placeholder: '123456789:ABC...' },
        ],
        Icon: SiTelegram,
        iconBg: '#229ed9',
        iconColor: '#ffffff',
    },
    {
        id: 'groupme',
        kind: 'native',
        name: 'GroupMe',
        description: 'Read-only context ingestion from your personal GroupMe group chats.',
        unlocks: ['Group chat context in memory', 'Catch up on school & club discussions', 'Natural recall without manual search'],
        command: [],
        credentials: [
            {
                name: 'GROUPME_ACCESS_TOKEN',
                description: 'Personal Access Token from dev.groupme.com/tokens',
                isSecret: true,
                placeholder: 'ba4d5b...',
            },
        ],
        Icon: SiGroupme,
        iconBg: '#00aff0',
        iconColor: '#ffffff',
    },
    {
        id: 'finance',
        kind: 'native',
        name: 'Finance (Plaid)',
        description: 'Connect bank accounts, credit cards, and investments via Plaid for balances, transactions, and net worth.',
        unlocks: ['Account balances & net worth', 'Categorized transactions', 'Investment holdings (Fidelity)', 'Proactive spending alerts'],
        command: [],
        credentials: [
            {
                name: 'PLAID_CLIENT_ID',
                description: 'Client ID from dashboard.plaid.com/team/keys',
                isSecret: false,
                placeholder: 'your_plaid_client_id',
            },
            {
                name: 'PLAID_SECRET',
                description: 'Sandbox or Production Secret from dashboard.plaid.com/team/keys',
                isSecret: true,
                placeholder: 'Plaid Secret Key',
            },
            {
                name: 'PLAID_ENV',
                description: "Environment: 'sandbox' or 'production' (default: sandbox)",
                isSecret: false,
                placeholder: 'sandbox',
            },
        ],
        Icon: null,
        iconBg: '#10b981',
        iconColor: '#ffffff',
    },
    {
        id: 'supabase',
        name: 'Supabase',
        description: 'Connect your Supabase account — query tables, run SQL, search docs, and manage Edge Functions.',
        unlocks: ['List & query tables', 'Run SQL & migrations', 'Search Supabase docs', 'Debug logs & advisors'],
        command: [
            'npx', '-y', 'mcp-remote',
            'https://mcp.supabase.com/mcp',
            '--auth-timeout', '120',
        ],
        credentials: [],
        oauth: true,
        extra: { connect_timeout_seconds: 130, timeout_seconds: 120 },
        Icon: null,
        iconBg: '#3ECF8E',
        iconColor: '#1a1a1a',
    },
    {
        id: 'canva',
        name: 'Canva',
        description: 'Create, edit, and export graphic designs — social posts, posters, flyers, and brand visuals.',
        unlocks: [
            'Generate editable designs from a brief',
            'Edit text/images in existing designs',
            'Export PNG/PDF/JPG (and more)',
            'Search your Canva library',
        ],
        command: [
            'npx', '-y', 'mcp-remote',
            'https://mcp.canva.com/mcp',
            '--auth-timeout', '120',
        ],
        credentials: [],
        oauth: true,
        extra: { connect_timeout_seconds: 130, timeout_seconds: 120 },
        Icon: null,
        iconBg: '#00C4CC',
        iconColor: '#ffffff',
    },
    // Coming soon
    {
        id: 'slack', name: 'Slack', description: 'Send messages and read channels in your workspace.',
        unlocks: [], command: [], credentials: [],
        Icon: SiSlack, iconBg: '#4a154b', iconColor: '#ffffff', comingSoon: true,
    },
    {
        id: 'linear', name: 'Linear', description: 'Create and track engineering issues and cycles.',
        unlocks: [], command: [], credentials: [],
        Icon: SiLinear, iconBg: '#5e6ad2', iconColor: '#ffffff', comingSoon: true,
    },
    {
        id: 'jira', name: 'Jira', description: 'Manage Jira tickets and sprint planning.',
        unlocks: [], command: [], credentials: [],
        Icon: SiJira, iconBg: '#0052cc', iconColor: '#ffffff', comingSoon: true,
    },
];

// ── Types ─────────────────────────────────────────────────────────────────────

type WizardStep = 'intro' | 'credentials' | 'testing' | 'success' | 'error' | 'manage';

interface WizardState {
    connector: ConnectorDef;
    step: WizardStep;
    credValues: Record<string, string>;
    testResult: { tools: string[]; error?: string } | null;
}

interface MCPServer { name: string; enabled: boolean; connected: boolean; configured?: boolean; }

interface NativeConnector { id: string; enabled: boolean; configured: boolean; connected: boolean; }

interface ConnectorsTabProps {
    servers: MCPServer[];
    nativeConnectors: NativeConnector[];
    onRefresh: () => void;
}

interface FinanceInstitution {
    item_id: string;
    institution_name: string;
    accounts?: unknown[];
}

type PlaidWindow = Window & {
    Plaid?: {
        create: (options: {
            token: string;
            onSuccess: (publicToken: string, metadata: Record<string, unknown>) => Promise<void>;
            onExit: () => void;
        }) => { open: () => void };
    };
};

// ── Component ─────────────────────────────────────────────────────────────────

export default function ConnectorsTab({ servers, nativeConnectors, onRefresh }: ConnectorsTabProps) {
    const [wizard, setWizard] = useState<WizardState | null>(null);
    const [financeInstitutions, setFinanceInstitutions] = useState<FinanceInstitution[]>([]);
    const [linkingBank, setLinkingBank] = useState(false);
    const api = window.pywebview?.api;

    const mcpByName = new Map(servers.map(s => [s.name, s]));
    const nativeById = new Map(nativeConnectors.map(n => [n.id, n]));

    const loadFinanceInstitutions = async () => {
        if (!api?.list_finance_institutions) return;
        try {
            const res = await api.list_finance_institutions() as { success?: boolean; institutions?: FinanceInstitution[] };
            if (res?.success && Array.isArray(res.institutions)) {
                setFinanceInstitutions(res.institutions);
            }
        } catch {
            // ignore
        }
    };

    const handleOpenPlaidLink = async () => {
        if (!api?.create_finance_link_token || !api?.exchange_finance_token) return;
        setLinkingBank(true);
        try {
            const plaidWindow = window as PlaidWindow;
            if (!plaidWindow.Plaid) {
                await new Promise<void>((resolve, reject) => {
                    const s = document.createElement('script');
                    s.src = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js';
                    s.onload = () => resolve();
                    s.onerror = () => reject(new Error('Failed to load Plaid Link SDK'));
                    document.body.appendChild(s);
                });
            }
            const res = await api.create_finance_link_token() as { success?: boolean; link_token?: string; error?: string };
            if (!res?.success || !res?.link_token) {
                alert(res?.error || 'Could not create Plaid Link session.');
                setLinkingBank(false);
                return;
            }
            if (!plaidWindow.Plaid) throw new Error('Plaid Link SDK did not initialize.');
            const handler = plaidWindow.Plaid.create({
                token: res.link_token,
                onSuccess: async (public_token: string, metadata: Record<string, unknown>) => {
                    await api.exchange_finance_token(public_token, metadata);
                    loadFinanceInstitutions();
                    onRefresh();
                    setLinkingBank(false);
                },
                onExit: () => {
                    setLinkingBank(false);
                },
            });
            handler.open();
        } catch (err) {
            alert(String(err));
            setLinkingBank(false);
        }
    };

    const isConnectorConnected = (conn: ConnectorDef): boolean => {
        if (conn.kind === 'native') {
            const row = nativeById.get(conn.id);
            return Boolean(row?.enabled && row?.configured);
        }
        const row = mcpByName.get(conn.id);
        return Boolean(row?.enabled && row?.configured);
    };

    const openWizard = (connector: ConnectorDef) => {
        if (connector.comingSoon) return;
        if (connector.id === 'finance') {
            loadFinanceInstitutions();
        }
        setWizard({
            connector,
            step: isConnectorConnected(connector) ? 'manage' : 'intro',
            credValues: {},
            testResult: null,
        });
    };

    const closeWizard = () => setWizard(null);

    const handleDisconnect = async (connector: ConnectorDef) => {
        if (connector.kind === 'native') {
            if (!api?.delete_native_connector) return;
            await api.delete_native_connector(connector.id);
        } else {
            if (!api?.delete_mcp_server) return;
            await api.delete_mcp_server(connector.id);
        }
        onRefresh();
        closeWizard();
    };

    const handleTest = async () => {
        if (!wizard || !api) return;
        const { connector, credValues } = wizard;

        if (connector.kind === 'native' && connector.id === 'gmail') {
            if (!api.start_google_auth) return;
            setWizard(w => w ? { ...w, step: 'testing' } : w);
            try {
                const result = await api.start_google_auth() as { success?: boolean; error?: string };
                if (result?.success && api.enable_native_connector) {
                    await api.enable_native_connector('gmail');
                    setWizard(w => w ? { ...w, step: 'success', testResult: { tools: [] } } : w);
                } else {
                    setWizard(w => w ? {
                        ...w,
                        step: 'error',
                        testResult: { tools: [], error: result?.error ?? 'Google sign-in failed' },
                    } : w);
                }
            } catch (e) {
                setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: String(e) } } : w);
            }
            return;
        }

        if (connector.kind === 'native' && connector.id === 'telegram') {
            if (!api.save_telegram_connector) return;
            const token = credValues.TELEGRAM_BOT_TOKEN ?? '';
            setWizard(w => w ? { ...w, step: 'testing' } : w);
            try {
                const result = await api.save_telegram_connector(token) as {
                    success?: boolean; error?: string; username?: string;
                };
                if (result?.success) {
                    setWizard(w => w ? {
                        ...w,
                        step: 'success',
                        testResult: { tools: result.username ? [`@${result.username}`] : [] },
                    } : w);
                } else {
                    setWizard(w => w ? {
                        ...w,
                        step: 'error',
                        testResult: { tools: [], error: result?.error ?? 'Invalid bot token' },
                    } : w);
                }
            } catch (e) {
                setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: String(e) } } : w);
            }
            return;
        }

        if (connector.kind === 'native' && connector.id === 'canvas') {
            if (!api.save_canvas_connector) return;
            setWizard(w => w ? { ...w, step: 'testing' } : w);
            try {
                const result = await api.save_canvas_connector(
                    credValues.CANVAS_BASE_URL ?? '',
                    credValues.CANVAS_TOKEN ?? '',
                    credValues.CANVAS_CONTACT_EMAIL ?? '',
                ) as { success?: boolean; error?: string; name?: string; login_id?: string };
                if (result?.success) {
                    const label = result.name
                        ? [result.name, result.login_id].filter(Boolean).join(' · ')
                        : 'Canvas connected';
                    setWizard(w => w ? {
                        ...w,
                        step: 'success',
                        testResult: { tools: [label] },
                    } : w);
                } else {
                    setWizard(w => w ? {
                        ...w,
                        step: 'error',
                        testResult: { tools: [], error: result?.error ?? 'Canvas connection failed' },
                    } : w);
                }
            } catch (e) {
                setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: String(e) } } : w);
            }
            return;
        }

        if (connector.kind === 'native' && connector.id === 'stock-images') {
            if (!api.save_stock_images_connector) return;
            setWizard(w => w ? { ...w, step: 'testing' } : w);
            try {
                const result = await api.save_stock_images_connector(
                    credValues.PEXELS_API_KEY ?? '',
                    credValues.UNSPLASH_ACCESS_KEY ?? '',
                    credValues.PIXABAY_API_KEY ?? '',
                ) as { success?: boolean; error?: string; name?: string; providers?: string[] };
                if (result?.success) {
                    const label = result.name || (result.providers || []).join(', ') || 'Stock images connected';
                    setWizard(w => w ? {
                        ...w,
                        step: 'success',
                        testResult: { tools: [label, 'Openverse always available (Services)'] },
                    } : w);
                } else {
                    setWizard(w => w ? {
                        ...w,
                        step: 'error',
                        testResult: { tools: [], error: result?.error ?? 'Stock images connection failed' },
                    } : w);
                }
            } catch (e) {
                setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: String(e) } } : w);
            }
            return;
        }

        if (connector.kind === 'native' && connector.id === 'finance') {
            if (!api.save_finance_connector) return;
            setWizard(w => w ? { ...w, step: 'testing' } : w);
            try {
                const result = await api.save_finance_connector(
                    credValues.PLAID_CLIENT_ID ?? '',
                    credValues.PLAID_SECRET ?? '',
                    credValues.PLAID_ENV ?? 'sandbox',
                ) as { success?: boolean; error?: string; message?: string };
                if (result?.success) {
                    onRefresh();
                    loadFinanceInstitutions();
                    setWizard(w => w ? {
                        ...w,
                        step: 'manage',
                    } : w);
                } else {
                    setWizard(w => w ? {
                        ...w,
                        step: 'error',
                        testResult: { tools: [], error: result?.error ?? 'Plaid validation failed' },
                    } : w);
                }
            } catch (e) {
                setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: String(e) } } : w);
            }
            return;
        }

        if (!api.test_mcp_connection) return;
        setWizard(w => w ? { ...w, step: 'testing' } : w);
        const env: Record<string, string> = {};
        for (const cred of connector.credentials) {
            if (credValues[cred.name]) env[cred.name] = credValues[cred.name];
        }
        try {
            const result = await api.test_mcp_connection(
                connector.id,
                connector.command,
                env,
                connector.extra ?? null,
            ) as { success: boolean; tools?: string[]; error?: string };
            if (result?.success) {
                setWizard(w => w ? { ...w, step: 'success', testResult: { tools: result.tools ?? [] } } : w);
            } else {
                setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: result?.error ?? 'Connection failed' } } : w);
            }
        } catch (e) {
            setWizard(w => w ? { ...w, step: 'error', testResult: { tools: [], error: String(e) } } : w);
        }
    };

    const handleConnect = async () => {
        if (!wizard || !api) return;
        const { connector, credValues } = wizard;

        if (connector.kind === 'native') {
            onRefresh();
            closeWizard();
            return;
        }

        if (!api.add_mcp_server) return;
        const env: Record<string, string> = {};
        for (const cred of connector.credentials) {
            if (credValues[cred.name]) env[cred.name] = credValues[cred.name];
        }
        await api.add_mcp_server(connector.id, connector.command, env, connector.extra ?? null);
        onRefresh();
        closeWizard();
    };

    const updateCred = (name: string, value: string) =>
        setWizard(w => w ? { ...w, credValues: { ...w.credValues, [name]: value } } : w);

    // ── Catalog ──────────────────────────────────────────────────────────────

    const renderIcon = (conn: ConnectorDef, size = 22) => {
        const Icon = conn.Icon ?? PlaceholderIcon;
        return (
            <div className="conn-icon-wrap" style={{ background: conn.iconBg }}>
                <Icon size={size} color={conn.iconColor} />
            </div>
        );
    };

    const renderCatalog = () => (
        <div className="conn-grid">
            {CONNECTOR_CATALOG.map(conn => {
                const isConnected = isConnectorConnected(conn);
                return (
                    <button
                        key={conn.id}
                        className={`conn-card ${conn.comingSoon ? 'conn-card-soon' : ''} ${isConnected ? 'conn-card-active' : 'conn-card-idle'}`}
                        onClick={() => openWizard(conn)}
                        disabled={conn.comingSoon}
                        title={conn.comingSoon ? 'Coming soon' : isConnected ? `${conn.name} — connected` : `${conn.name} — not connected`}
                    >
                        {renderIcon(conn)}
                        <div className="conn-card-body">
                            <span className="conn-card-name">{conn.name}</span>
                            <span className="conn-card-desc">{conn.description}</span>
                        </div>
                        {isConnected && (
                            <span className="conn-connected-badge">
                                <CheckCircle2 size={12} /> Connected
                            </span>
                        )}
                        {!isConnected && !conn.comingSoon && (
                            <span className="conn-idle-badge">Not connected</span>
                        )}
                        {conn.comingSoon && (
                            <span className="conn-soon-badge">Soon</span>
                        )}
                    </button>
                );
            })}
        </div>
    );

    // ── Wizard ────────────────────────────────────────────────────────────────

    const renderWizard = () => {
        if (!wizard) return null;
        const { connector, step, credValues, testResult } = wizard;

        return (
            <div className="conn-overlay" onClick={e => { if (e.target === e.currentTarget) closeWizard(); }}>
                <div className="conn-wizard">

                    {/* Header */}
                    <div className="conn-wiz-header">
                        {renderIcon(connector, 20)}
                        <div className="conn-wiz-title-block">
                            <h3 className="conn-wiz-title">{connector.name}</h3>
                            <p className="conn-wiz-sub">{connector.description}</p>
                        </div>
                        <button className="conn-wiz-close" onClick={closeWizard}>✕</button>
                    </div>

                    {/* MANAGE */}
                    {step === 'manage' && (
                        <div className="conn-wiz-body">
                            <div className="conn-wiz-connected">
                                <CheckCircle2 size={15} /> Connected
                            </div>
                            <div className="conn-wiz-section">
                                <p className="conn-wiz-label">Capabilities</p>
                                <ul className="conn-wiz-list">
                                    {connector.unlocks.map(u => <li key={u}>{u}</li>)}
                                </ul>
                            </div>
                            {connector.id === 'finance' && (
                                <div className="conn-wiz-section">
                                    <p className="conn-wiz-label">Linked Financial Institutions</p>
                                    {financeInstitutions.length === 0 ? (
                                        <p className="conn-wiz-needs" style={{ marginBottom: '12px' }}>
                                            No bank or investment accounts linked yet. Click below to connect via Plaid Link (Capital One, SoFi, Fidelity, etc.).
                                        </p>
                                    ) : (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
                                            {financeInstitutions.map((inst) => (
                                                <div
                                                    key={inst.item_id}
                                                    style={{
                                                        display: 'flex',
                                                        justifyContent: 'space-between',
                                                        alignItems: 'center',
                                                        background: 'rgba(255,255,255,0.05)',
                                                        padding: '8px 12px',
                                                        borderRadius: '6px',
                                                        border: '1px solid rgba(255,255,255,0.1)',
                                                    }}
                                                >
                                                    <div>
                                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{inst.institution_name}</div>
                                                        <div style={{ fontSize: '11px', opacity: 0.7 }}>{(inst.accounts || []).length} account(s) connected</div>
                                                    </div>
                                                    <button
                                                        className="conn-inst-remove-btn"
                                                        title={`Remove ${inst.institution_name}`}
                                                        onClick={async () => {
                                                            if (api?.disconnect_finance_institution) {
                                                                await api.disconnect_finance_institution(inst.item_id);
                                                                loadFinanceInstitutions();
                                                                onRefresh();
                                                            }
                                                        }}
                                                    >
                                                        <Trash2 size={12} />
                                                        <span>Remove</span>
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    <button
                                        className="mcp-btn mcp-btn-primary"
                                        disabled={linkingBank}
                                        onClick={handleOpenPlaidLink}
                                        style={{ width: '100%', justifyContent: 'center' }}
                                    >
                                        {linkingBank ? <Loader2 size={14} className="spin" /> : '+ Link Bank Account'}
                                    </button>
                                </div>
                            )}
                            <div className="conn-wiz-actions">
                                <button className="mcp-btn conn-disconnect-btn" onClick={() => handleDisconnect(connector)}>
                                    <Unplug size={13} /> Disconnect
                                </button>
                                {connector.credentials.length > 0 && (
                                    <button
                                        className="mcp-btn mcp-btn-secondary"
                                        onClick={() => setWizard(w => w ? { ...w, step: 'credentials' } : w)}
                                    >
                                        Edit Credentials
                                    </button>
                                )}
                                <button className="mcp-btn mcp-btn-secondary" onClick={closeWizard}>Done</button>
                            </div>
                        </div>
                    )}

                    {/* INTRO */}
                    {step === 'intro' && (
                        <div className="conn-wiz-body">
                            <div className="conn-wiz-section">
                                <p className="conn-wiz-label">What you can do</p>
                                <ul className="conn-wiz-list">
                                    {connector.unlocks.map(u => <li key={u}>{u}</li>)}
                                </ul>
                            </div>
                            {connector.credentials.length > 0 && (
                                <p className="conn-wiz-needs">
                                    Requires: {connector.credentials.map(c => c.name).join(', ')}
                                </p>
                            )}
                            {connector.oauth && (
                                <p className="conn-wiz-needs">
                                    {oauthIntroCopy(connector)}
                                </p>
                            )}
                            {connector.kind === 'native' && connector.id === 'gmail' && (
                                <p className="conn-wiz-needs">
                                    Place your Google Cloud OAuth <code>credentials.json</code> at <code>config/credentials.json</code>, then click Connect to sign in via browser.
                                    For push notifications, see <code>docs/setup/CONNECTORS.md</code> (Gmail webhook section).
                                </p>
                            )}
                            <div className="conn-wiz-actions">
                                <button className="mcp-btn mcp-btn-secondary" onClick={closeWizard}>Cancel</button>
                                <button
                                    className="mcp-btn mcp-btn-primary"
                                    onClick={() => {
                                        if (connector.kind === 'native' && (connector.id === 'telegram' || connector.id === 'canvas' || connector.id === 'stock-images' || connector.id === 'finance')) {
                                            setWizard(w => w ? { ...w, step: 'credentials' } : w);
                                        } else if (connector.credentials.length === 0) {
                                            handleTest();
                                        } else {
                                            setWizard(w => w ? { ...w, step: 'credentials' } : w);
                                        }
                                    }}
                                >
                                    {connector.credentials.length === 0 && connector.kind !== 'native'
                                        ? 'Connect'
                                        : connector.kind === 'native' && connector.id === 'gmail'
                                            ? 'Connect'
                                            : 'Set Up'} <ChevronRight size={14} />
                                </button>
                            </div>
                        </div>
                    )}

                    {/* CREDENTIALS */}
                    {step === 'credentials' && (
                        <div className="conn-wiz-body">
                            <p className="conn-wiz-label">Enter your credentials</p>
                            {connector.credentials.map(cred => (
                                <div key={cred.name} className="mcp-field">
                                    <label>{cred.name}</label>
                                    <span className="conn-cred-hint">{cred.description}</span>
                                    <input
                                        type={cred.isSecret ? 'password' : 'text'}
                                        value={credValues[cred.name] ?? ''}
                                        onChange={e => updateCred(cred.name, e.target.value)}
                                        placeholder={cred.placeholder}
                                        className="mcp-input"
                                        autoComplete={cred.isSecret ? 'new-password' : 'off'}
                                    />
                                </div>
                            ))}
                            <div className="conn-wiz-actions">
                                <button className="mcp-btn mcp-btn-secondary" onClick={() => setWizard(w => w ? { ...w, step: isConnectorConnected(w.connector) ? 'manage' : 'intro' } : w)}>Back</button>
                                <button className="mcp-btn mcp-btn-primary" onClick={handleTest}>
                                    {connector.id === 'finance' ? 'Connect' : 'Test Connection'} <ChevronRight size={14} />
                                </button>
                            </div>
                        </div>
                    )}

                    {/* TESTING */}
                    {step === 'testing' && (
                        <div className="conn-wiz-body conn-wiz-status">
                            <Loader2 size={30} className="mcp-spin conn-wiz-status-icon" />
                            <p className="conn-wiz-status-text">Connecting to {connector.name}…</p>
                            <p className="conn-wiz-status-sub">
                                {connector.oauth
                                    ? 'Complete sign-in in your browser, then select the org and projects to grant access. This may take up to 2 minutes.'
                                    : 'This may take up to 15 seconds'}
                            </p>
                        </div>
                    )}

                    {/* SUCCESS */}
                    {step === 'success' && (
                        <div className="conn-wiz-body conn-wiz-status">
                            <CheckCircle2 size={30} className="conn-wiz-ok-icon" />
                            <p className="conn-wiz-status-text">Connection verified!</p>
                            {(testResult?.tools?.length ?? 0) > 0 && (
                                <p className="conn-wiz-status-sub">
                                    {testResult!.tools.length} tool{testResult!.tools.length !== 1 ? 's' : ''} available
                                </p>
                            )}
                            <div className="conn-wiz-actions">
                                <button className="mcp-btn mcp-btn-primary" onClick={() => {
                                    if (connector.id === 'finance') {
                                        onRefresh();
                                        loadFinanceInstitutions();
                                        setWizard(w => w ? { ...w, step: 'manage' } : w);
                                        return;
                                    }
                                    if (connector.kind === 'native') {
                                        onRefresh();
                                        closeWizard();
                                    } else {
                                        handleConnect();
                                    }
                                }}>
                                    {connector.id === 'finance' ? 'Continue' : connector.kind === 'native' ? 'Done' : 'Save & Activate'}
                                </button>
                            </div>
                        </div>
                    )}

                    {/* ERROR */}
                    {step === 'error' && (
                        <div className="conn-wiz-body conn-wiz-status">
                            <XCircle size={30} className="conn-wiz-err-icon" />
                            <p className="conn-wiz-status-text">Connection failed</p>
                            {testResult?.error && (
                                <p className="conn-wiz-err-detail">{testResult.error}</p>
                            )}
                            <div className="conn-wiz-actions">
                                <button className="mcp-btn mcp-btn-secondary" onClick={closeWizard}>Cancel</button>
                                <button
                                    className="mcp-btn mcp-btn-primary"
                                    onClick={() => setWizard(w => w ? {
                                        ...w,
                                        step: connector.credentials.length > 0 ? 'credentials' : 'intro',
                                        testResult: null,
                                    } : w)}
                                >
                                    Try Again
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        );
    };

    return (
        <div className="connectors-tab">
            <p className="connectors-intro">
                Connect services to unlock tools for Chad. All connectors are optional — grey tiles are available but not yet configured.
            </p>
            {renderCatalog()}
            {wizard && renderWizard()}
        </div>
    );
}
