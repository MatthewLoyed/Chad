import { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, Power, ChevronDown, ChevronUp, Search, Download, Loader2, CheckCircle2 } from 'lucide-react';
import { readCache, writeCache } from '../../../utils/localCache';
import './MCPServersTab.css';
import ConnectorsTab from './ConnectorsTab';

interface MCPServer {
    name: string;
    command: string[];
    env: Record<string, string>;
    timeout_seconds?: number;
    min_delay_seconds?: number;
    enabled: boolean;
    connected: boolean;
    configured?: boolean;
}

interface NativeConnector {
    id: string;
    enabled: boolean;
    configured: boolean;
    connected: boolean;
}

interface ToolCatalogEntry {
    name: string;
    source?: string;
    server_name?: string | null;
}

interface RegistryServer {
    id: string;
    title: string;
    description: string;
    iconUrl?: string;
    packages: RegistryPackage[];
}

interface RegistryPackage {
    registryType: string;
    identifier: string;
    runtimeHint?: string;
    environmentVariables?: RegistryEnvVar[];
}

interface RegistryEnvVar {
    name: string;
    description: string;
    isRequired: boolean;
    isSecret: boolean;
    default?: string;
}

type TabView = 'connectors' | 'installed' | 'marketplace';

interface EnvVarField {
    name: string;
    value: string;
}

interface EditFormState {
    name: string;
    commandStr: string;
    envVars: EnvVarField[];
}

const REGISTRY_BASE = 'https://registry.modelcontextprotocol.io/v0';
const CACHE_KEY = 'mcp_registry_cache';
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

interface RegistryCache {
    ts: number;
    servers: RegistryServer[];
    cursor: string | null;
}

function loadCache(): RegistryCache | null {
    try {
        const raw = localStorage.getItem(CACHE_KEY);
        if (!raw) return null;
        const parsed: RegistryCache = JSON.parse(raw);
        if (Date.now() - parsed.ts > CACHE_TTL_MS) {
            localStorage.removeItem(CACHE_KEY);
            return null;
        }
        return parsed;
    } catch {
        return null;
    }
}

function saveCache(servers: RegistryServer[], cursor: string | null) {
    try {
        const cache: RegistryCache = { ts: Date.now(), servers, cursor };
        localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
    } catch { /* storage full — skip */ }
}

interface RegistryIcon {
    type?: string;
    size?: string;
    url?: string;
}

interface ServerWithIcons {
    icons?: RegistryIcon[];
}

function pickIcon(server: ServerWithIcons | null | undefined): string | undefined {
    const icons = server?.icons ?? [];
    // Prefer small/medium PNG, fall back to any
    const preferred = icons.find(i => i.type?.includes('png') && (i.size === 'small' || i.size === 'medium' || !i.size));
    return (preferred ?? icons[0])?.url;
}

function pickPackage(packages: RegistryPackage[]): RegistryPackage | undefined {
    // Prefer npx/npm, then uvx/pypi, then anything
    return (
        packages.find(p => p.runtimeHint === 'npx' || p.registryType === 'npm') ??
        packages.find(p => p.runtimeHint === 'uvx' || p.registryType === 'pypi') ??
        packages[0]
    );
}

function buildCommand(pkg: RegistryPackage): string[] {
    if (pkg.runtimeHint === 'uvx' || pkg.registryType === 'pypi') {
        return ['uvx', pkg.identifier];
    }
    return ['npx', '-y', pkg.identifier];
}

const CACHE_KEY_MCP = 'mcp_servers';
const CACHE_KEY_NATIVE = 'native_connectors';

interface MCPServersTabProps {
    initialServerId?: string;
}

export default function MCPServersTab({ initialServerId }: MCPServersTabProps) {
    const [view, setView] = useState<TabView>('connectors');
    const [servers, setServers] = useState<MCPServer[]>(() => readCache<MCPServer[]>(CACHE_KEY_MCP) || []);
    const [nativeConnectors, setNativeConnectors] = useState<NativeConnector[]>(() => readCache<NativeConnector[]>(CACHE_KEY_NATIVE) || []);
    const [loading, setLoading] = useState(false);

    // Installed tab state
    const [expandedServer, setExpandedServer] = useState<string | null>(null);
    const [editForm, setEditForm] = useState<EditFormState | null>(null);
    const [addingNew, setAddingNew] = useState(false);

    // Marketplace state
    const [registryServers, setRegistryServers] = useState<RegistryServer[]>([]);
    const [registrySearch, setRegistrySearch] = useState('');
    const [registryCursor, setRegistryCursor] = useState<string | null>(null);
    const [registryLoading, setRegistryLoading] = useState(false);
    const [registryHasMore, setRegistryHasMore] = useState(true);
    const [installTarget, setInstallTarget] = useState<RegistryServer | null>(null);
    const [installEnvVars, setInstallEnvVars] = useState<EnvVarField[]>([]);

    const [toolsByServer, setToolsByServer] = useState<Record<string, string[]>>({});

    const api = window.pywebview?.api;

    const installedNames = new Set(servers.map(s => s.name));

    const fetchInstalled = useCallback(() => {
        if (!api?.get_mcp_servers) return;
        setLoading(true);
        Promise.all([
            api.get_mcp_servers(),
            api.get_native_connectors?.() ?? Promise.resolve([]),
        ]).then(([mcpResult, nativeResult]) => {
            const resultServers = Array.isArray(mcpResult) ? mcpResult as MCPServer[] : [];
            const resultNative = Array.isArray(nativeResult) ? nativeResult as NativeConnector[] : [];
            setServers(resultServers);
            setNativeConnectors(resultNative);
            writeCache(CACHE_KEY_MCP, resultServers);
            writeCache(CACHE_KEY_NATIVE, resultNative);
        }).catch(console.error).finally(() => setLoading(false));
    }, [api]);

    useEffect(() => {
        fetchInstalled();
    }, [fetchInstalled]);

    // Cross-reference which tools each MCP server powers, for the "Powers: x, y" line.
    useEffect(() => {
        if (!api?.list_tool_catalog) return;
        api.list_tool_catalog().then((result: unknown) => {
            const entries = Array.isArray(result) ? (result as ToolCatalogEntry[]) : [];
            const grouped: Record<string, string[]> = {};
            for (const entry of entries) {
                if (entry.source !== 'mcp' || !entry.server_name) continue;
                (grouped[entry.server_name] ??= []).push(entry.name);
            }
            setToolsByServer(grouped);
        }).catch(() => {});
    }, [api]);

    // When Chad pushes a specific server ID, switch to marketplace and auto-open its install dialog
    useEffect(() => {
        if (!initialServerId) return;
        setView('marketplace'); // deep-link from suggest_mcp_install always goes to marketplace
        const fetchAndInstall = async () => {
            try {
                // Search by ID directly
                const res = await fetch(`${REGISTRY_BASE}/servers/${encodeURIComponent(initialServerId)}/versions/latest`);
                if (res.ok) {
                    const data = await res.json();
                    const server: RegistryServer = {
                        id: data.name ?? initialServerId,
                        title: data.title ?? initialServerId,
                        description: data.description ?? '',
                        iconUrl: pickIcon(data),
                        packages: data.packages ?? [],
                    };
                    handleInstallClick(server);
                    return;
                }
            } catch { /* fall through to search */ }
            // Fallback: search by name
            try {
                const res = await fetch(`${REGISTRY_BASE}/servers?search=${encodeURIComponent(initialServerId)}&limit=1`);
                if (res.ok) {
                    const data = await res.json();
                    const first = data.servers?.[0]?.server;
                    if (first) {
                        const server: RegistryServer = {
                            id: first.name ?? initialServerId,
                            title: first.title ?? initialServerId,
                            description: first.description ?? '',
                            iconUrl: pickIcon(first),
                            packages: first.packages ?? [],
                        };
                        handleInstallClick(server);
                    }
                }
            } catch (e) {
                console.error('Failed to fetch server for auto-install:', e);
            }
        };
        fetchAndInstall();
    }, [initialServerId]);

    // --- Installed tab actions ---

    const handleToggle = (name: string, enabled: boolean, configured = true) => {
        if (!api?.toggle_mcp_server) return;
        if (!enabled && !configured) return;
        api.toggle_mcp_server(name, !enabled).then(() => fetchInstalled());
    };

    const handleDelete = (name: string) => {
        if (!api?.delete_mcp_server) return;
        if (!confirm(`Remove "${name}"?`)) return;
        api.delete_mcp_server(name).then(() => fetchInstalled());
    };

    const handleExpand = (name: string, server: MCPServer) => {
        if (expandedServer === name) {
            setExpandedServer(null);
            setEditForm(null);
        } else {
            setExpandedServer(name);
            setEditForm({
                name,
                commandStr: server.command.join(' '),
                envVars: Object.entries(server.env).map(([k, v]) => ({ name: k, value: v })),
            });
        }
    };

    const handleSaveEdit = () => {
        if (!editForm || !api?.update_mcp_server) return;
        const command = editForm.commandStr.trim().split(/\s+/);
        const env: Record<string, string> = {};
        for (const { name, value } of editForm.envVars) {
            if (name.trim()) env[name.trim()] = value;
        }
        api.update_mcp_server(editForm.name, command, env).then(() => {
            setExpandedServer(null);
            setEditForm(null);
            fetchInstalled();
        });
    };

    const handleAddNew = () => {
        setAddingNew(true);
        setEditForm({ name: '', commandStr: 'npx -y ', envVars: [] });
    };

    const handleSaveNew = () => {
        if (!editForm || !api?.add_mcp_server) return;
        const command = editForm.commandStr.trim().split(/\s+/);
        const env: Record<string, string> = {};
        for (const { name, value } of editForm.envVars) {
            if (name.trim()) env[name.trim()] = value;
        }
        api.add_mcp_server(editForm.name.trim(), command, env).then(() => {
            setAddingNew(false);
            setEditForm(null);
            fetchInstalled();
        });
    };

    const addEnvVar = () => {
        setEditForm(f => f ? { ...f, envVars: [...f.envVars, { name: '', value: '' }] } : f);
    };

    const removeEnvVar = (idx: number) => {
        setEditForm(f => f ? { ...f, envVars: f.envVars.filter((_, i) => i !== idx) } : f);
    };

    const updateEnvVar = (idx: number, field: 'name' | 'value', val: string) => {
        setEditForm(f => {
            if (!f) return f;
            const envVars = f.envVars.map((e, i) => i === idx ? { ...e, [field]: val } : e);
            return { ...f, envVars };
        });
    };

    // --- Marketplace ---

    const fetchRegistry = useCallback(async (search: string, cursor: string | null, append: boolean) => {
        // On initial load (no search, no cursor, not appending), try cache first
        if (!search && !cursor && !append) {
            const cached = loadCache();
            if (cached) {
                setRegistryServers(cached.servers);
                setRegistryCursor(cached.cursor);
                setRegistryHasMore(!!cached.cursor);
                return;
            }
        }

        setRegistryLoading(true);
        try {
            const params = new URLSearchParams({ limit: '20' });
            if (search) params.set('search', search);
            if (cursor) params.set('cursor', cursor);
            const res = await fetch(`${REGISTRY_BASE}/servers?${params}`);
            if (!res.ok) throw new Error(`Registry error: ${res.status}`);
            const data = await res.json();
            const items: RegistryServer[] = (data.servers ?? []).map((s: unknown) => {
                const item = s as {
                    server?: {
                        name?: string;
                        title?: string;
                        description?: string;
                        icons?: RegistryIcon[];
                        packages?: RegistryPackage[];
                    };
                };
                return {
                    id: item.server?.name ?? '',
                    title: item.server?.title ?? item.server?.name ?? '',
                    description: item.server?.description ?? '',
                    iconUrl: pickIcon(item.server),
                    packages: item.server?.packages ?? [],
                };
            });
            const next = data.metadata?.nextCursor ?? null;
            const merged = append ? [...registryServers, ...items] : items;
            setRegistryServers(merged);
            setRegistryCursor(next);
            setRegistryHasMore(!!next);
            // Cache only the baseline (no search, first page)
            if (!search && !append) saveCache(merged, next);
        } catch (e) {
            console.error(e);
        } finally {
            setRegistryLoading(false);
        }
    }, [registryServers]);

    useEffect(() => {
        if (view === 'marketplace' && registryServers.length === 0) {
            fetchRegistry('', null, false);
        }
    }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleRegistrySearch = (q: string) => {
        setRegistrySearch(q);
        setRegistryServers([]);
        setRegistryCursor(null);
        fetchRegistry(q, null, false);
    };

    const handleLoadMore = () => {
        if (registryCursor) fetchRegistry(registrySearch, registryCursor, true);
    };

    const handleInstallClick = (server: RegistryServer) => {
        const pkg = pickPackage(server.packages);
        const envFields: EnvVarField[] = (pkg?.environmentVariables ?? [])
            .filter(e => e.isRequired)
            .map(e => ({ name: e.name, value: e.default ?? '' }));
        setInstallTarget(server);
        setInstallEnvVars(envFields);
    };

    const handleConfirmInstall = () => {
        if (!installTarget || !api?.add_mcp_server) return;
        const pkg = pickPackage(installTarget.packages);
        if (!pkg) return;
        const command = buildCommand(pkg);
        const env: Record<string, string> = {};
        for (const { name, value } of installEnvVars) {
            if (name.trim()) env[name.trim()] = value;
        }
        const serverName = installTarget.id.replace(/^.*\//, '').replace(/[@]/g, '').toLowerCase();
        api.add_mcp_server(serverName, command, env).then(() => {
            setInstallTarget(null);
            setInstallEnvVars([]);
            fetchInstalled();
            setView('installed');
        });
    };

    // --- Render helpers ---

    const renderEditForm = (isNew: boolean) => (
        <div className="mcp-edit-form">
            {isNew && (
                <div className="mcp-field">
                    <label>Server Name</label>
                    <input
                        value={editForm?.name ?? ''}
                        onChange={e => setEditForm(f => f ? { ...f, name: e.target.value } : f)}
                        placeholder="my-server"
                        className="mcp-input"
                    />
                </div>
            )}
            <div className="mcp-field">
                <label>Command</label>
                <input
                    value={editForm?.commandStr ?? ''}
                    onChange={e => setEditForm(f => f ? { ...f, commandStr: e.target.value } : f)}
                    placeholder="npx -y @org/server-name"
                    className="mcp-input mcp-input-mono"
                />
            </div>
            <div className="mcp-field">
                <label>Environment Variables</label>
                {editForm?.envVars.map((ev, idx) => (
                    <div key={idx} className="mcp-env-row">
                        <input
                            value={ev.name}
                            onChange={e => updateEnvVar(idx, 'name', e.target.value)}
                            placeholder="KEY"
                            className="mcp-input mcp-input-mono mcp-env-key"
                        />
                        <input
                            value={ev.value}
                            onChange={e => updateEnvVar(idx, 'value', e.target.value)}
                            placeholder="value"
                            className="mcp-input mcp-input-mono mcp-env-val"
                        />
                        <button className="mcp-icon-btn mcp-delete-btn" onClick={() => removeEnvVar(idx)}>
                            <Trash2 size={13} />
                        </button>
                    </div>
                ))}
                <button className="mcp-add-env-btn" onClick={addEnvVar}>+ Add variable</button>
            </div>
            <div className="mcp-form-actions">
                <button className="mcp-btn mcp-btn-secondary" onClick={() => { setExpandedServer(null); setEditForm(null); setAddingNew(false); }}>
                    Cancel
                </button>
                <button className="mcp-btn mcp-btn-primary" onClick={isNew ? handleSaveNew : handleSaveEdit}>
                    {isNew ? 'Add Server' : 'Save'}
                </button>
            </div>
        </div>
    );

    return (
        <div className="mcp-tab">
            {/* Sub-tab switcher */}
            <div className="mcp-subtabs">
                <button className={`mcp-subtab ${view === 'connectors' ? 'active' : ''}`} onClick={() => setView('connectors')}>
                    Connectors
                </button>
                <button className={`mcp-subtab ${view === 'installed' ? 'active' : ''}`} onClick={() => setView('installed')}>
                    Installed
                </button>
                <button className={`mcp-subtab ${view === 'marketplace' ? 'active' : ''}`} onClick={() => setView('marketplace')}>
                    Marketplace
                </button>
            </div>

            {/* ── Connectors view ── */}
            {view === 'connectors' && (
                <ConnectorsTab servers={servers} nativeConnectors={nativeConnectors} onRefresh={fetchInstalled} />
            )}

            {/* ── Installed view ── */}
            {view === 'installed' && (
                <div className="mcp-installed">
                    {loading && <div className="mcp-loading"><Loader2 size={16} className="mcp-spin" /> Loading...</div>}

                    {!loading && servers.length === 0 && !addingNew && (
                        <div className="mcp-empty">No MCP servers configured. Add one below or install from the Marketplace.</div>
                    )}

                    {servers.map(server => (
                        <div key={server.name} className={`mcp-server-card ${!server.enabled ? 'mcp-disabled' : ''}`}>
                            <div className="mcp-server-header">
                                <div className="mcp-server-meta">
                                    <span className={`mcp-status-dot ${server.connected ? 'connected' : server.enabled ? 'idle' : 'off'}`} />
                                    <span className="mcp-server-name">{server.name}</span>
                                    <span className="mcp-server-cmd">{server.command.slice(0, 3).join(' ')}</span>
                                </div>
                                <div className="mcp-server-actions">
                                    <button
                                        className={`mcp-toggle ${server.enabled ? 'on' : 'off'}`}
                                        onClick={() => handleToggle(server.name, server.enabled, server.configured !== false)}
                                        disabled={!server.enabled && server.configured === false}
                                        title={server.configured === false ? 'Configure credentials first' : server.enabled ? 'Disable' : 'Enable'}
                                    >
                                        <Power size={13} />
                                    </button>
                                    <button className="mcp-icon-btn" onClick={() => handleExpand(server.name, server)} title="Edit">
                                        {expandedServer === server.name ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                                    </button>
                                    <button className="mcp-icon-btn mcp-delete-btn" onClick={() => handleDelete(server.name)} title="Remove">
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                            </div>
                            {(toolsByServer[server.name]?.length ?? 0) > 0 && (
                                <div className="mcp-server-powers">
                                    Powers:{' '}
                                    {toolsByServer[server.name].map((t, i) => (
                                        <span key={t}>
                                            {i > 0 && ', '}
                                            <code>{t}</code>
                                        </span>
                                    ))}
                                </div>
                            )}
                            {expandedServer === server.name && editForm && renderEditForm(false)}
                        </div>
                    ))}

                    {addingNew && editForm && (
                        <div className="mcp-server-card mcp-new-card">
                            <div className="mcp-server-header">
                                <span className="mcp-server-name">New Server</span>
                            </div>
                            {renderEditForm(true)}
                        </div>
                    )}

                    {!addingNew && (
                        <button className="mcp-add-server-btn" onClick={handleAddNew}>
                            <Plus size={15} /> Add Server
                        </button>
                    )}
                </div>
            )}

            {/* ── Marketplace view ── */}
            {view === 'marketplace' && (
                <div className="mcp-marketplace">
                    {/* Install dialog */}
                    {installTarget && (
                        <div className="mcp-install-dialog">
                            <div className="mcp-install-header">
                                {installTarget.iconUrl && (
                                    <img src={installTarget.iconUrl} alt="" className="mcp-install-icon" onError={e => (e.currentTarget.style.display = 'none')} />
                                )}
                                <h4 className="mcp-install-title">Install: {installTarget.title}</h4>
                            </div>
                            <p className="mcp-install-desc">{installTarget.description}</p>
                            {installEnvVars.length > 0 && (
                                <>
                                    <p className="mcp-install-label">Required credentials:</p>
                                    {installEnvVars.map((ev, idx) => (
                                        <div key={idx} className="mcp-field">
                                            <label>{ev.name}</label>
                                            <input
                                                type="password"
                                                value={ev.value}
                                                onChange={e => setInstallEnvVars(vars => vars.map((v, i) => i === idx ? { ...v, value: e.target.value } : v))}
                                                placeholder={`Your ${ev.name}`}
                                                className="mcp-input"
                                            />
                                        </div>
                                    ))}
                                </>
                            )}
                            <div className="mcp-form-actions">
                                <button className="mcp-btn mcp-btn-secondary" onClick={() => setInstallTarget(null)}>Cancel</button>
                                <button className="mcp-btn mcp-btn-primary" onClick={handleConfirmInstall}>
                                    <Download size={13} /> Install
                                </button>
                            </div>
                        </div>
                    )}

                    {!installTarget && (
                        <>
                            <div className="mcp-search-row">
                                <Search size={14} className="mcp-search-icon" />
                                <input
                                    className="mcp-search-input"
                                    placeholder="Search MCP servers..."
                                    value={registrySearch}
                                    onChange={e => handleRegistrySearch(e.target.value)}
                                />
                            </div>

                            <div className="mcp-registry-list">
                                {registryServers.map(server => {
                                    const isInstalled = installedNames.has(
                                        server.id.replace(/^.*\//, '').replace(/[@]/g, '').toLowerCase()
                                    );
                                    return (
                                        <div key={server.id} className={`mcp-registry-card ${isInstalled ? 'mcp-registry-installed' : ''}`}>
                                            {server.iconUrl && (
                                                <img
                                                    src={server.iconUrl}
                                                    alt=""
                                                    className="mcp-registry-icon"
                                                    onError={e => (e.currentTarget.style.display = 'none')}
                                                />
                                            )}
                                            <div className="mcp-registry-info">
                                                <span className="mcp-registry-title">{server.title}</span>
                                                <span className="mcp-registry-desc">{server.description}</span>
                                            </div>
                                            {isInstalled ? (
                                                <span className="mcp-installed-badge">
                                                    <CheckCircle2 size={13} /> Installed
                                                </span>
                                            ) : (
                                                <button className="mcp-btn mcp-btn-primary mcp-install-btn" onClick={() => handleInstallClick(server)}>
                                                    Install
                                                </button>
                                            )}
                                        </div>
                                    );
                                })}
                                {registryLoading && (
                                    <div className="mcp-loading"><Loader2 size={16} className="mcp-spin" /> Loading...</div>
                                )}
                                {!registryLoading && registryHasMore && (
                                    <button className="mcp-load-more-btn" onClick={handleLoadMore}>Load more</button>
                                )}
                                {!registryLoading && registryServers.length === 0 && (
                                    <div className="mcp-empty">No results.</div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
