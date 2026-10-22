import { useState, useEffect, useMemo } from 'react';
import { motion } from 'framer-motion';
import { Volume2, VolumeX, BrainCircuit, Database, Plug, Bot, Palette, Rocket, Check, X, Search, RefreshCw, BookOpen, Shield, Server, ShieldCheck } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import ContextMeter from '../data-display/ContextMeter';
import Dropdown from '../common/Dropdown';
import MemoryCatalog from './MemoryCatalog';
import MCPServersTab from '../modals/settings/MCPServersTab';
import CapabilitiesTab from '../modals/settings/CapabilitiesTab';
import ModelsTab from '../modals/settings/ModelsTab';
import SubagentsTab from '../modals/settings/SubagentsTab';
import BrowserSafetyTab from '../modals/settings/BrowserSafetyTab';
import PresentonSettingsTab from '../modals/settings/PresentonSettingsTab';
import ChadCodeTab from '../modals/settings/ChadCodeTab';
import SupabaseSettingsTab from '../modals/settings/SupabaseSettingsTab';
import SkillsCatalog from './SkillsCatalog';
import PairPhoneCard from './PairPhoneCard';
import { ACCENT_PRESETS, applyAccent, getStoredAccent } from '../../utils/accent';
import { useAppStore, useVoiceAvailability } from '../../store';
import type { VoiceAvailability } from '../../store';
import { readCache, writeCache } from '../../utils/localCache';
import '../modals/memory/MemoryModal.css';
import './SettingsView.css';

type CategoryId = 'integrations' | 'capabilities' | 'system' | 'services' | 'models' | 'memory' | 'audio' | 'subagents' | 'browser_safety' | 'skills' | 'appearance';

type CategoryDeepLink = CategoryId;

function resolveSettingsCategory(
    initialServerId: string | undefined,
    initialCategory: CategoryDeepLink | undefined,
): CategoryId {
    if (initialServerId) return 'integrations';
    return initialCategory ?? 'system';
}

interface SettingsViewProps {
    onClose: () => void;
    onUseSkill?: (name: string) => void;
    initialServerId?: string; // when set, opens Integrations with this server's install dialog
    initialCategory?: CategoryDeepLink; // deep-link, e.g. from the command palette's "Open Memory"
}

interface AudioDevice {
    id: string;
    name: string;
    active?: boolean;
}

interface ServiceHealth {
    id: string;
    label: string;
    tier: 'core' | 'enhanced' | 'optional';
    healthy: boolean;
    detail: string | null;
    mode?: 'local' | 'cloud';
}

type MicTestResult = {
    success: boolean;
    heard?: boolean;
    peak_level?: number;
    message?: string;
};

type WakeWordTestResult = {
    success: boolean;
    detected?: boolean;
    message?: string;
};

const VOICE_OPTIONS = [
    { value: 'Puck', label: 'Puck (Energetic)' },
    { value: 'Charon', label: 'Charon (Deep)' },
    { value: 'Kore', label: 'Kore (Calm)' },
    { value: 'Fenrir', label: 'Fenrir (Intense)' },
    { value: 'Aoede', label: 'Aoede (Clear)' },
];

type CategoryDef = { id: CategoryId; label: string; icon: LucideIcon; blurb: string; keywords: string };

const CATEGORY_SECTIONS: { title: string; ids: CategoryId[] }[] = [
    { title: 'Account', ids: ['integrations', 'system', 'services'] },
    { title: 'Core', ids: ['models', 'memory', 'capabilities', 'audio'] },
    { title: 'Advanced', ids: ['subagents', 'browser_safety', 'skills'] },
    { title: 'Personal', ids: ['appearance'] },
];

const CATEGORIES: CategoryDef[] = [
    { id: 'integrations', label: 'Integrations', icon: Plug, blurb: 'MCP servers and external tools.', keywords: 'mcp connect plugin server' },
    { id: 'capabilities', label: 'Capabilities', icon: ShieldCheck, blurb: 'Built-in local tools you opt into — off by default.', keywords: 'browser history chrome ingestion sync privacy opt-in local capability' },
    { id: 'system', label: 'System', icon: Rocket, blurb: 'Updates and production deployment.', keywords: 'version update restart auto' },
    { id: 'services', label: 'Services', icon: Server, blurb: 'Health of Chad\'s infrastructure dependencies.', keywords: 'searxng kroki typst marp lm studio health brain presenton chad code coding openhands docker' },
    { id: 'models', label: 'Models & Routing', icon: BrainCircuit, blurb: 'Cloud and local models Chad reasons with.', keywords: 'llm local lm studio gemini' },
    { id: 'memory', label: 'Memory & Context', icon: Database, blurb: 'Context budget, stored memories, and optional Supabase upgrade.', keywords: 'facts remember context profile supabase cloud memory' },
    { id: 'audio', label: 'Audio & Voice', icon: Volume2, blurb: 'Microphone, AI voice, and volume.', keywords: 'mic speaker tts voice' },
    { id: 'subagents', label: 'Subagents', icon: Bot, blurb: 'Specialist agents and their models.', keywords: 'agent specialist delegate' },
    { id: 'browser_safety', label: 'Browser Safety', icon: Shield, blurb: 'Approval mode and trusted domains for browser_agent.', keywords: 'browser playwright allowlist approval autonomy' },
    { id: 'skills', label: 'Skills', icon: BookOpen, blurb: 'Domain expertise guides Chad loads on demand.', keywords: 'expertise guides workflow catalog' },
    { id: 'appearance', label: 'Appearance', icon: Palette, blurb: 'Accent color and visual style.', keywords: 'theme color purple accent' },
];

/**
 * Settings as a full-stage page (was a modal). Bigger, categorized, and — because
 * it lives in the stage rather than a floating overlay — navigating to any
 * workspace cleanly replaces it (App routes nav through one handler that closes
 * this). MCP-install pushes still deep-link to Integrations via initialServerId.
 */
export default function SettingsView({ onClose, initialServerId, initialCategory, onUseSkill }: SettingsViewProps) {
    // Deep-link to Integrations when an MCP install pushed us open, or to a specific
    // category (e.g. Memory/Usage from the command palette). The page mounts fresh
    // on each open (App gates it), so the initializer sees the right value.
    const [category, setCategory] = useState<CategoryId>(
        resolveSettingsCategory(initialServerId, initialCategory)
    );
    const [visitedCategories, setVisitedCategories] = useState<Set<CategoryId>>(
        () => new Set([resolveSettingsCategory(initialServerId, initialCategory)])
    );
    useEffect(() => {
        setVisitedCategories((prev) => {
            if (prev.has(category)) return prev;
            const next = new Set(prev);
            next.add(category);
            return next;
        });
    }, [category]);
    const [settingsSearch, setSettingsSearch] = useState('');

    const [devices, setDevices] = useState<AudioDevice[]>(() => readCache<AudioDevice[]>('audio_devices') ?? []);
    const [devicesLoading, setDevicesLoading] = useState(() => !readCache<AudioDevice[]>('audio_devices'));
    const [devicesError, setDevicesError] = useState<string | null>(null);
    const [selectedDevice, setSelectedDevice] = useState('');
    const [selectedVoice, setSelectedVoice] = useState('Kore');
    const [volume, setVolume] = useState(100);
    const [micTesting, setMicTesting] = useState(false);
    const [micLevel, setMicLevel] = useState(0);
    const [micTestResult, setMicTestResult] = useState<MicTestResult | null>(null);
    const [wakeTesting, setWakeTesting] = useState(false);
    const [wakeTestResult, setWakeTestResult] = useState<WakeWordTestResult | null>(null);
    const [contextProfile, setContextProfile] = useState('balanced');
    const [accent, setAccent] = useState(getStoredAccent);

    // Memory Core (folded in from the old MemoryModal — #165)
    const [memorySearch, setMemorySearch] = useState('');

    // Always-armed "Hey Chad" wake word (issue #654). null = not loaded yet.
    // The pipeline (mic, wake word, STT) lives in the desktop Limb, so the whole
    // section hides on the browser/PWA client rather than spinning forever.
    const [wakeWordSupported, setWakeWordSupported] = useState(false);
    const [wakeWordEnabled, setWakeWordEnabled] = useState<boolean | null>(() => readCache<boolean>('wake_word_enabled'));
    const [wakeWordSaving, setWakeWordSaving] = useState(false);
    const [wakeWordNeedsRestart, setWakeWordNeedsRestart] = useState(false);
    const [announceAutonomous, setAnnounceAutonomous] = useState<boolean>(() => readCache<boolean>('announce_autonomous') ?? true);
    const [announceAutonomousSaving, setAnnounceAutonomousSaving] = useState(false);
    const voiceAvailability = useVoiceAvailability();
    const setVoiceAvailability = useAppStore((s) => s.setVoiceAvailability);

    // System — one-click production update
    const [updating, setUpdating] = useState(false);
    const [updateResult, setUpdateResult] = useState<string | null>(null);
    // System — deployed version + auto-update toggle
    type VersionSnapshot = {
        sha?: string;
        deployed_sha?: string;
        branch?: string;
        date?: string;
        needs_restart?: boolean;
        deployed?: { sha?: string; branch?: string; date?: string };
        running?: { sha?: string; started_at?: string; pid?: number };
        gui?: { sha?: string; started_at?: string };
        stale?: { brain?: boolean; gui?: boolean };
    };
    const [sysVersion, setSysVersion] = useState<VersionSnapshot | null>(() => readCache<VersionSnapshot>('system_version'));
    const [autoUpdate, setAutoUpdate] = useState<boolean | null>(() => readCache<boolean>('system_auto_update'));
    const [isDevStack, setIsDevStack] = useState(false);
    const [remoteBranches, setRemoteBranches] = useState<string[]>([]);
    const [selectedBranch, setSelectedBranch] = useState('master');
    const [branchSwitching, setBranchSwitching] = useState(false);
    const [branchResult, setBranchResult] = useState<string | null>(null);
    const [systemLoadError, setSystemLoadError] = useState<string | null>(null);
    const [services, setServices] = useState<ServiceHealth[] | null>(() => readCache<ServiceHealth[]>('services_health'));
    const [servicesError, setServicesError] = useState<string | null>(null);
    const [servicesLoading, setServicesLoading] = useState(false);
    const [searxngStarting, setSearxngStarting] = useState(false);
    const [searxngMessage, setSearxngMessage] = useState<string | null>(null);
    const [searchBackend, setSearchBackend] = useState(() => readCache<string>('searxng_backend') ?? 'searxng');
    const [searchBackendSaving, setSearchBackendSaving] = useState(false);
    type SearchQuotaEntry = {
        configured: boolean; available: boolean; note?: string;
        used?: number; limit?: number; remaining?: number; plan?: string; error?: string;
    };
    const [searchQuota, setSearchQuota] = useState<Record<string, SearchQuotaEntry> | null>(() => readCache<Record<string, SearchQuotaEntry>>('search_quota'));
    const [searchQuotaLoading, setSearchQuotaLoading] = useState(false);

    type UpdateResponse = {
        success: boolean; updated?: boolean; from?: string; to?: string;
        steps?: string[]; message?: string; error?: string; restarting?: boolean;
        gui_restarting?: boolean; gui_restart_recommended?: boolean;
    };

    const applyUpdateResponse = async (r: UpdateResponse, setResult: (msg: string) => void) => {
        const api = window.pywebview?.api;
        if (!r.success) {
            setResult(`Update failed: ${r.error ?? 'unknown error'}`);
            return;
        }
        if (r.gui_restarting || r.gui_restart_recommended || r.restarting) {
            const msg = r.message ?? 'Update applied — restarting Chad…';
            setResult(msg);
            await new Promise((res) => setTimeout(res, 2000));
            await api?.trigger_restart?.();
            return;
        }
        setResult(r.message ?? 'Already up to date — Brain and GUI are running the latest code.');
    };

    // Load version + auto-update state when the System tab is opened.
    useEffect(() => {
        if (category !== 'system') return;
        const api = window.pywebview?.api;
        if (!api?.get_system_settings) return;
        setSystemLoadError(null);
        (async () => {
            try {
                const r = (await api.get_system_settings()) as {
                    success?: boolean;
                    version?: VersionSnapshot;
                    auto_update?: boolean;
                    dev_stack?: boolean;
                    dev_worktree?: boolean;
                    error?: string;
                };
                if (!r?.success) {
                    setSystemLoadError(r?.error ?? 'Could not load system settings');
                    return;
                }

                const dev = r.dev_stack === true;
                setIsDevStack(dev);

                if (r?.version) {
                    setSysVersion(r.version);
                    writeCache('system_version', r.version);
                    if (r.version.branch) setSelectedBranch(r.version.branch);
                }
                if (typeof r?.auto_update === 'boolean') {
                    setAutoUpdate(r.auto_update);
                    writeCache('system_auto_update', r.auto_update);
                }

                if (dev && typeof api.get_branches === 'function') {
                    const branches = (await api.get_branches()) as {
                        success?: boolean;
                        current?: string;
                        branches?: string[];
                        error?: string;
                    };
                    if (branches?.success && Array.isArray(branches.branches)) {
                        setRemoteBranches(branches.branches);
                        if (branches.current) setSelectedBranch(branches.current);
                    } else if (branches?.error) {
                        setBranchResult(`Could not load branches: ${branches.error}`);
                    }
                }
            } catch (e) {
                setSystemLoadError(String(e));
            }
        })();
    }, [category]);

    const loadServicesHealth = async () => {
        const api = window.pywebview?.api;
        if (!api?.get_services_health) return;
        setServicesLoading(true);
        setServicesError(null);
        try {
            const r = (await api.get_services_health()) as { services?: ServiceHealth[]; error?: string };
            if (r?.error) {
                setServicesError(r.error);
            } else {
                const list = r?.services ?? [];
                setServices(list);
                writeCache('services_health', list);
            }
        } catch (e) {
            setServicesError(String(e));
        } finally {
            setServicesLoading(false);
        }
    };

    const loadSearchQuota = async () => {
        const api = window.pywebview?.api;
        if (!api?.get_search_quota) return;
        setSearchQuotaLoading(true);
        try {
            const r = (await api.get_search_quota()) as Record<string, SearchQuotaEntry>;
            const q = r ?? {};
            setSearchQuota(q);
            writeCache('search_quota', q);
        } catch {
            // Non-fatal — quota badges just stay hidden.
        } finally {
            setSearchQuotaLoading(false);
        }
    };

    // Load service health + the current search backend when the Services tab is opened.
    useEffect(() => {
        if (category !== 'services') return;
        loadServicesHealth();
        loadSearchQuota();
        (async () => {
            const api = window.pywebview?.api;
            if (!api?.get_searxng_settings) return;
            try {
                const r = (await api.get_searxng_settings()) as { search_backend?: string };
                if (r?.search_backend) {
                    setSearchBackend(r.search_backend);
                    writeCache('searxng_backend', r.search_backend);
                }
            } catch {
                // Non-fatal — dropdown just keeps its default.
            }
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [category]);

    const handleSearchBackendChange = async (value: string) => {
        const previous = searchBackend;
        setSearchBackend(value); // optimistic
        writeCache('searxng_backend', value);
        const api = window.pywebview?.api;
        if (!api?.set_searxng_settings) return;
        setSearchBackendSaving(true);
        try {
            const r = (await api.set_searxng_settings(value)) as { success?: boolean; error?: string };
            if (!r?.success) {
                setSearchBackend(previous); // revert on rejection (e.g. invalid value)
                writeCache('searxng_backend', previous);
                setSearxngMessage(r?.error ?? 'Failed to update search backend.');
            }
        } catch (e) {
            setSearchBackend(previous);
            writeCache('searxng_backend', previous);
            setSearxngMessage(String(e));
        } finally {
            setSearchBackendSaving(false);
        }
    };

    const handleEnableSearxng = async () => {
        const api = window.pywebview?.api;
        if (!api?.start_searxng_sidecar) return;
        setSearxngStarting(true);
        setSearxngMessage(null);
        try {
            const r = (await api.start_searxng_sidecar()) as { success?: boolean; message?: string };
            setSearxngMessage(r?.message ?? (r?.success ? 'Started.' : 'Failed to start.'));
            if (r?.success) await loadServicesHealth();
        } catch (e) {
            setSearxngMessage(String(e));
        } finally {
            setSearxngStarting(false);
        }
    };

    const handleAutoUpdateToggle = async () => {
        const api = window.pywebview?.api;
        if (!api?.set_auto_update) return;
        const next = !autoUpdate;
        setAutoUpdate(next); // optimistic
        try {
            const r = (await api.set_auto_update(next)) as { success?: boolean; auto_update?: boolean };
            if (typeof r?.auto_update === 'boolean') {
                setAutoUpdate(r.auto_update);
                writeCache('system_auto_update', r.auto_update);
            }
        } catch {
            setAutoUpdate(!next); // revert on failure
        }
    };

    // Close on Escape.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    const loadAudioDevices = async () => {
        const api = window.pywebview?.api;
        if (!api?.get_audio_devices) {
            setDevicesLoading(false);
            setDevicesError('Audio settings require the desktop app.');
            return;
        }
        setDevicesLoading(true);
        setDevicesError(null);
        try {
            const raw = await api.get_audio_devices();
            if (!Array.isArray(raw)) {
                setDevices([]);
                setDevicesError('Could not read microphone list.');
                return;
            }
            const normalized: AudioDevice[] = raw.map((d: Record<string, unknown>) => ({
                id: String(d.id ?? d.index ?? ''),
                name: String(d.name ?? 'Unknown device'),
                active: Boolean(d.active),
            })).filter((d) => d.id !== '');
            setDevices(normalized);
            writeCache('audio_devices', normalized);
            const active = normalized.find((d) => d.active);
            if (active) {
                setSelectedDevice(active.id);
            } else if (normalized.length === 1) {
                setSelectedDevice(normalized[0].id);
            }
            if (normalized.length === 0) {
                setDevicesError('No microphones found. Check Windows sound settings.');
            }
        } catch (e) {
            setDevicesError(String(e));
        } finally {
            setDevicesLoading(false);
        }
    };

    const loadAudioPreferences = async () => {
        const api = window.pywebview?.api;
        if (!api) return;
        if (typeof api.get_voice === 'function') {
            api.get_voice().then((r) => typeof r === 'string' && setSelectedVoice(r)).catch(console.error);
        }
        if (typeof api.get_volume === 'function') {
            api.get_volume().then((r) => typeof r === 'number' && setVolume(r)).catch(console.error);
        }
        if (typeof api.get_wake_word_mode === 'function') {
            setWakeWordSupported(true);
            api.get_wake_word_mode()
                .then((r) => {
                    const res = r as { enabled?: boolean; availability?: VoiceAvailability | null };
                    if (typeof res?.enabled === 'boolean') {
                        setWakeWordEnabled(res.enabled);
                        writeCache('wake_word_enabled', res.enabled);
                    }
                    // The live push (window.handleVoiceAvailability) is the primary
                    // source; this backfills when Settings is opened after the push.
                    if (res?.availability) setVoiceAvailability(res.availability);
                })
                .catch(console.error);
        }
        if (typeof api.get_autonomous_announcements === 'function') {
            api.get_autonomous_announcements()
                .then((r) => {
                    if (typeof r === 'boolean') {
                        setAnnounceAutonomous(r);
                        writeCache('announce_autonomous', r);
                    }
                })
                .catch(console.error);
        }
    };

    const handleAnnounceAutonomousToggle = async () => {
        const api = window.pywebview?.api;
        if (!api?.set_autonomous_announcements) return;
        const next = !announceAutonomous;
        setAnnounceAutonomousSaving(true);
        try {
            const r = (await api.set_autonomous_announcements(next)) as { success?: boolean; enabled?: boolean };
            const enabled = (r?.success && typeof r.enabled === 'boolean') ? r.enabled : next;
            setAnnounceAutonomous(enabled);
            writeCache('announce_autonomous', enabled);
        } catch (e) {
            console.error(e);
        } finally {
            setAnnounceAutonomousSaving(false);
        }
    };

    const handleWakeWordToggle = async () => {
        const api = window.pywebview?.api;
        if (!api?.set_wake_word_mode || wakeWordEnabled === null) return;
        const next = !wakeWordEnabled;
        setWakeWordSaving(true);
        try {
            const r = (await api.set_wake_word_mode(next)) as { success?: boolean; requires_restart?: boolean };
            if (r?.success) {
                setWakeWordEnabled(next);
                writeCache('wake_word_enabled', next);
                setWakeWordNeedsRestart(!!r.requires_restart);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setWakeWordSaving(false);
        }
    };

    // Load audio hardware when the Audio tab is opened (pywebview may not be ready on first mount).
    useEffect(() => {
        if (category !== 'audio') return;
        loadAudioDevices();
        loadAudioPreferences();
    }, [category]);

    useEffect(() => {
        if (window.pywebview?.api) {
            const api = window.pywebview.api;
            if (typeof api.get_context_status === 'function') {
                api.get_context_status().then((r: unknown) => {
                    const data = r as { profile?: string };
                    if (data?.profile) setContextProfile(data.profile);
                }).catch(console.error);
            }
        }
    }, []);

    const handleDeviceChange = (id: string) => {
        setSelectedDevice(id);
        setMicTestResult(null);
        window.pywebview?.api?.set_audio_device?.(id);
    };
    const handleVoiceChange = (v: string) => { setSelectedVoice(v); window.pywebview?.api?.set_voice?.(v); };
    const handleVolumeChange = (v: number) => { setVolume(v); window.pywebview?.api?.set_volume?.(v); };
    const handleProfileChange = (p: string) => { setContextProfile(p); window.pywebview?.api?.set_context_profile?.(p); };
    const handleAccentChange = (id: string) => { setAccent(id); applyAccent(id); };

    const handleUpdate = async () => {
        const api = window.pywebview?.api;
        if (!api?.update_chad) {
            setUpdateResult('Update is only available from the desktop app.');
            return;
        }
        setUpdating(true);
        setUpdateResult('Checking for updates and applying — Chad will restart when done…');
        try {
            const r = (await api.update_chad()) as UpdateResponse;
            await applyUpdateResponse(r, setUpdateResult);
        } catch (e) {
            setUpdateResult(`Update failed: ${String(e)}`);
        } finally {
            setUpdating(false);
        }
    };

    const handleBranchSwitch = async (branch: string) => {
        const api = window.pywebview?.api;
        if (!api?.update_chad) {
            setBranchResult('Branch switching is only available from the desktop app.');
            return;
        }
        setBranchSwitching(true);
        setBranchResult(`Switching to ${branch} — Chad will restart when done…`);
        try {
            const r = (await api.update_chad(branch)) as UpdateResponse;
            await applyUpdateResponse(r, setBranchResult);
        } catch (e) {
            setBranchResult(`Switch failed: ${String(e)}`);
        } finally {
            setBranchSwitching(false);
        }
    };

    const branchOptions = useMemo(() => {
        const names = new Set(remoteBranches);
        if (selectedBranch) names.add(selectedBranch);
        names.add('master');
        return [...names].sort().map((name) => ({ value: name, label: name }));
    }, [remoteBranches, selectedBranch]);

    const formatStarted = (iso?: string) => {
        if (!iso) return null;
        try {
            return new Date(iso).toLocaleString();
        } catch {
            return null;
        }
    };

    const deviceOptions = devicesLoading
        ? [{ value: '', label: 'Scanning devices…' }]
        : devices.length > 0
            ? devices.map((d) => ({ value: d.id, label: d.name }))
            : [{ value: '', label: devicesError ? 'No microphones found' : 'No devices' }];

    const handleMicTest = async () => {
        const api = window.pywebview?.api;
        if (!api?.test_microphone) {
            setMicTestResult({ success: false, message: 'Microphone test requires the desktop app.' });
            return;
        }
        setMicTesting(true);
        setMicTestResult(null);
        setMicLevel(0);

        const poll = window.setInterval(() => {
            api.get_audio_level?.().then((level) => {
                if (typeof level === 'number') setMicLevel(level);
            }).catch(() => {});
        }, 100);

        try {
            const result = (await api.test_microphone(selectedDevice || null, 3)) as MicTestResult;
            setMicTestResult(result);
            if (typeof result.peak_level === 'number') setMicLevel(result.peak_level);
        } catch (e) {
            setMicTestResult({ success: false, message: String(e) });
        } finally {
            window.clearInterval(poll);
            setMicTesting(false);
        }
    };

    const handleWakeWordTest = async () => {
        const api = window.pywebview?.api;
        if (!api?.test_wake_word) {
            setWakeTestResult({ success: false, message: 'Wake word test requires the desktop app.' });
            return;
        }
        setWakeTesting(true);
        setWakeTestResult(null);
        try {
            const result = (await api.test_wake_word(selectedDevice || null, 10)) as WakeWordTestResult;
            setWakeTestResult(result);
        } catch (e) {
            setWakeTestResult({ success: false, message: String(e) });
        } finally {
            setWakeTesting(false);
        }
    };

    const profileOptions = [
        { value: 'aggressive', label: 'Aggressive (Cost Saver)' },
        { value: 'balanced', label: 'Balanced (Recommended)' },
        { value: 'permissive', label: 'Permissive (High Context)' },
    ];

    const searchBackendOptions = [
        { value: 'searxng', label: 'SearXNG (Free, self-hosted — default)' },
        { value: 'all', label: 'All API keys (rotate Serper/Tavily/Brave, no SearXNG)' },
        { value: 'brave', label: 'Brave only' },
        { value: 'serper', label: 'Serper only' },
        { value: 'tavily', label: 'Tavily only' },
    ];

    const active = useMemo(() => CATEGORIES.find((c) => c.id === category) ?? CATEGORIES[0], [category]);

    const visibleSections = useMemo(() => {
        const q = settingsSearch.trim().toLowerCase();
        if (!q) return CATEGORY_SECTIONS;
        return CATEGORY_SECTIONS.map((section) => ({
            ...section,
            ids: section.ids.filter((id) => {
                const cat = CATEGORIES.find((c) => c.id === id);
                if (!cat) return false;
                return (
                    cat.label.toLowerCase().includes(q) ||
                    cat.blurb.toLowerCase().includes(q) ||
                    cat.keywords.toLowerCase().includes(q) ||
                    id.includes(q)
                );
            }),
        })).filter((s) => s.ids.length > 0);
    }, [settingsSearch]);

    return (
        <motion.div
            className="settings-page"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
        >
            <header className="settings-page__top">
                <div>
                    <h1 className="settings-page__title">Settings</h1>
                    <p className="settings-page__subtitle">Everything that shapes how Chad looks and works.</p>
                </div>
                <button type="button" className="settings-page__close" onClick={onClose} title="Close (Esc)">
                    <X size={18} />
                </button>
            </header>

            <div className="settings-page__layout">
                <nav className="settings-rail" aria-label="Settings categories">
                    <div className="settings-rail__search">
                        <Search size={15} className="settings-rail__search-icon" />
                        <input
                            type="search"
                            className="settings-rail__search-input"
                            placeholder="Search settings…"
                            value={settingsSearch}
                            onChange={(e) => setSettingsSearch(e.target.value)}
                            aria-label="Search settings"
                        />
                    </div>
                    {visibleSections.map(({ title, ids }) => (
                        <div key={title} className="settings-rail__section">
                            <div className="settings-rail__section-title">{title}</div>
                            {ids.map((id) => {
                                const cat = CATEGORIES.find((c) => c.id === id)!;
                                const Icon = cat.icon;
                                return (
                                    <button
                                        key={id}
                                        type="button"
                                        className={`settings-rail__item ${category === id ? 'is-active' : ''}`}
                                        onClick={() => setCategory(id)}
                                    >
                                        <Icon size={16} />
                                        <span>{cat.label}</span>
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                </nav>

                <div className={`settings-content ${category === 'models' || category === 'subagents' || category === 'skills' ? 'settings-content--wide' : ''}`} data-scrollable="true">
                    <div className="settings-content__head">
                        <h2 className="settings-content__title">{active.label}</h2>
                        <p className="settings-content__blurb">{active.blurb}</p>
                    </div>

                    {visitedCategories.has('audio') && (
                        <div style={{ display: category === 'audio' ? undefined : 'none' }}>
                            {wakeWordSupported && (
                            <div className="form-group">
                                <label className="form-label">Hey Chad (Always Listening)</label>
                                <p className="settings-hint">
                                    When on, Chad listens for &ldquo;Hey Chad&rdquo; the whole time the app is
                                    running and handles the command by voice. Runs fully locally.
                                </p>
                                <button
                                    type="button"
                                    className="settings-update-btn"
                                    onClick={handleWakeWordToggle}
                                    disabled={wakeWordEnabled === null || wakeWordSaving}
                                    aria-pressed={!!wakeWordEnabled}
                                >
                                    {wakeWordEnabled === null
                                        ? 'Loading…'
                                        : wakeWordSaving
                                            ? 'Applying…'
                                            : wakeWordEnabled ? 'Hey Chad: On' : 'Hey Chad: Off'}
                                </button>

                                {/* The status line that used to exist only in the logs:
                                    whether the pipeline actually armed, and what to fix. */}
                                {wakeWordEnabled && voiceAvailability && (
                                    <p className={`settings-hint settings-audio-result ${voiceAvailability.armed ? 'is-success' : 'is-error'}`}>
                                        {voiceAvailability.armed
                                            ? 'Armed — say "Hey Chad" any time.'
                                            : [voiceAvailability.message, voiceAvailability.detail, voiceAvailability.remedy]
                                                .filter(Boolean)
                                                .join(' — ')}
                                    </p>
                                )}
                                {wakeWordNeedsRestart && (
                                    <p className="settings-hint settings-audio-result is-warn">
                                        Saved, but voice mode could not start in this session. Restart Chad to apply.
                                    </p>
                                )}
                            </div>
                            )}

                            <div className="form-group">
                                <div className="settings-audio-head">
                                    <label className="form-label">Input Device (Microphone)</label>
                                    <button
                                        type="button"
                                        className="settings-memory-refresh"
                                        onClick={loadAudioDevices}
                                        disabled={devicesLoading}
                                        title="Rescan microphones"
                                    >
                                        <RefreshCw size={14} className={devicesLoading ? 'is-spinning' : ''} />
                                        Refresh
                                    </button>
                                </div>
                                <Dropdown
                                    value={selectedDevice}
                                    options={deviceOptions}
                                    onChange={handleDeviceChange}
                                    placeholder="Select Microphone"
                                    disabled={devicesLoading || devices.length === 0}
                                />
                                {devicesError && (
                                    <p className="settings-hint settings-update-result">{devicesError}</p>
                                )}
                            </div>

                            <div className="form-group settings-audio-test">
                                <label className="form-label">Test Microphone</label>
                                <p className="settings-hint">Speak normally for a few seconds to confirm Chad can hear you.</p>
                                <div className="mic-level-track" aria-hidden={!micTesting && micLevel === 0}>
                                    <div
                                        className="mic-level-fill"
                                        style={{ width: `${Math.min(100, Math.round(micLevel * 400))}%` }}
                                    />
                                </div>
                                <button
                                    type="button"
                                    className="settings-update-btn"
                                    onClick={handleMicTest}
                                    disabled={micTesting || devices.length === 0}
                                >
                                    {micTesting ? 'Listening…' : 'Test Microphone'}
                                </button>
                                {micTestResult && (
                                    <p className={`settings-hint settings-audio-result ${micTestResult.heard ? 'is-success' : micTestResult.success ? 'is-warn' : 'is-error'}`}>
                                        {micTestResult.message}
                                    </p>
                                )}
                            </div>

                            <div className="form-group settings-audio-test">
                                <label className="form-label">Test Wake Word</label>
                                <p className="settings-hint">Say &ldquo;Hey Chad&rdquo; to verify the wake word detector picks you up.</p>
                                <button
                                    type="button"
                                    className="settings-update-btn settings-update-btn--secondary"
                                    onClick={handleWakeWordTest}
                                    disabled={wakeTesting || devices.length === 0}
                                >
                                    {wakeTesting ? 'Listening for Hey Chad…' : 'Test Wake Word'}
                                </button>
                                {wakeTestResult && (
                                    <p className={`settings-hint settings-audio-result ${wakeTestResult.detected ? 'is-success' : wakeTestResult.success ? 'is-warn' : 'is-error'}`}>
                                        {wakeTestResult.message}
                                    </p>
                                )}
                            </div>

                            <div className="form-group">
                                <label className="form-label">AI Voice</label>
                                <Dropdown value={selectedVoice} options={VOICE_OPTIONS} onChange={handleVoiceChange} />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Master Volume</label>
                                <div className="slider-container">
                                    <input type="range" className="slider-input" min="0" max="100" value={volume} onChange={(e) => handleVolumeChange(Number(e.target.value))} />
                                    <span className="slider-value">{volume}%</span>
                                </div>
                            </div>

                            <div className="form-group">
                                <label className="form-label">Announce Autonomous Actions</label>
                                <p className="settings-hint">
                                    When enabled, Chad reads aloud autonomous notifications and scheduled task updates
                                    using TTS. Turn off to keep autonomous actions silent (visual notifications only).
                                </p>
                                <button
                                    type="button"
                                    className={`settings-update-btn ${announceAutonomous ? '' : 'settings-update-btn--secondary'}`}
                                    onClick={handleAnnounceAutonomousToggle}
                                    disabled={announceAutonomousSaving}
                                    aria-pressed={announceAutonomous}
                                >
                                    {announceAutonomous ? <Volume2 size={16} /> : <VolumeX size={16} />}
                                    {announceAutonomousSaving
                                        ? 'Saving…'
                                        : announceAutonomous ? 'Announce Actions: On' : 'Announce Actions: Muted'}
                                </button>
                            </div>
                        </div>
                    )}

                    {visitedCategories.has('models') && (
                        <div style={{ display: category === 'models' ? undefined : 'none' }}>
                            <ModelsTab />
                        </div>
                    )}

                    {visitedCategories.has('memory') && (
                        <div style={{ display: category === 'memory' ? undefined : 'none' }}>
                            <div className="form-group">
                                <label className="form-label">Cloud Memory (Supabase)</label>
                                <SupabaseSettingsTab />
                            </div>

                            <div className="form-group">
                                <label className="form-label">Memory Budget (Auto-Sleep)</label>
                                <Dropdown value={contextProfile} options={profileOptions} onChange={handleProfileChange} />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Live Context</label>
                                <ContextMeter />
                            </div>

                            <div className="form-group">
                                <label className="form-label">Memory Core</label>
                                <div className="memory-search">
                                    <Search size={16} className="memory-search__icon" />
                                    <input
                                        type="text"
                                        placeholder="Search memories..."
                                        value={memorySearch}
                                        onChange={(e) => setMemorySearch(e.target.value)}
                                        className="memory-search__input"
                                    />
                                </div>
                                <MemoryCatalog active={category === 'memory'} searchQuery={memorySearch} />
                            </div>
                        </div>
                    )}

                    {visitedCategories.has('integrations') && (
                        <div style={{ display: category === 'integrations' ? undefined : 'none' }}>
                            <MCPServersTab initialServerId={initialServerId} />
                        </div>
                    )}

                    {visitedCategories.has('capabilities') && (
                        <div style={{ display: category === 'capabilities' ? undefined : 'none' }}>
                            <CapabilitiesTab />
                        </div>
                    )}

                    {visitedCategories.has('subagents') && (
                        <div style={{ display: category === 'subagents' ? undefined : 'none' }}>
                            <SubagentsTab />
                        </div>
                    )}

                    {visitedCategories.has('browser_safety') && (
                        <div style={{ display: category === 'browser_safety' ? undefined : 'none' }}>
                            <BrowserSafetyTab />
                        </div>
                    )}

                    {visitedCategories.has('skills') && (
                        <div style={{ display: category === 'skills' ? undefined : 'none' }}>
                            <SkillsCatalog onUseSkill={onUseSkill} />
                        </div>
                    )}

                    {visitedCategories.has('system') && (
                        <div className="form-group" style={{ display: category === 'system' ? undefined : 'none' }}>
                            {systemLoadError && (
                                <p className="settings-hint settings-update-result">{systemLoadError}</p>
                            )}

                            <PairPhoneCard />

                            {isDevStack ? (
                                <div className="settings-dev-panel">
                                    <p className="settings-dev-badge">Chad Dev - separate copy from prod</p>
                                    <p className="settings-hint">
                                        Pick a pushed branch and restart. Prod Chad on <code>master</code> is not affected.
                                    </p>
                                    <div className="settings-dev-current">
                                        <span className="settings-dev-current__label">Current branch</span>
                                        <span className="settings-dev-current__name">{selectedBranch || '…'}</span>
                                        {sysVersion?.deployed?.sha && (
                                            <span className="settings-dev-current__sha">{sysVersion.deployed.sha}</span>
                                        )}
                                    </div>
                                    <Dropdown
                                        value={selectedBranch}
                                        options={branchOptions}
                                        onChange={setSelectedBranch}
                                        placeholder="Select branch…"
                                        disabled={branchSwitching || branchOptions.length === 0}
                                    />
                                    <div className="settings-branch-actions">
                                        <button
                                            type="button"
                                            className="settings-update-btn"
                                            onClick={() => handleBranchSwitch(selectedBranch)}
                                            disabled={branchSwitching || !selectedBranch}
                                        >
                                            <RefreshCw size={16} className={branchSwitching ? 'is-spinning' : ''} />
                                            {branchSwitching ? 'Switching…' : 'Switch & restart'}
                                        </button>
                                        <button
                                            type="button"
                                            className="settings-update-btn settings-update-btn--secondary"
                                            onClick={() => handleBranchSwitch('master')}
                                            disabled={branchSwitching}
                                        >
                                            Back to master
                                        </button>
                                    </div>
                                    {branchResult && (
                                        <p className="settings-hint settings-update-result">{branchResult}</p>
                                    )}
                                </div>
                            ) : (
                                <>
                            <label className="form-label">Version</label>
                            {sysVersion?.stale?.brain && (
                                <p className="settings-hint settings-update-result" style={{ marginBottom: '0.5rem' }}>
                                    Brain restart needed — disk has <code>{sysVersion.deployed?.sha ?? sysVersion.deployed_sha}</code>{' '}
                                    but the server is still running <code>{sysVersion.running?.sha ?? sysVersion.sha}</code>.
                                    Click Update Chad below.
                                </p>
                            )}
                            <p className="settings-hint">
                                {sysVersion?.running?.sha || sysVersion?.sha ? (
                                    <>
                                        Brain running <code>{sysVersion.running?.sha ?? sysVersion.sha}</code>
                                        {sysVersion.running?.started_at && (
                                            <> · started {formatStarted(sysVersion.running.started_at)}</>
                                        )}
                                        {sysVersion.deployed?.sha &&
                                            sysVersion.deployed.sha !== (sysVersion.running?.sha ?? sysVersion.sha) && (
                                                <> · disk <code>{sysVersion.deployed.sha}</code></>
                                            )}
                                        {sysVersion.branch ? ` (${sysVersion.branch})` : ''}
                                    </>
                                ) : (
                                    'Checking version…'
                                )}
                            </p>
                            {sysVersion?.gui?.sha && (
                                <p className="settings-hint">
                                    GUI running <code>{sysVersion.gui.sha}</code>
                                    {sysVersion.gui.started_at && (
                                        <> · started {formatStarted(sysVersion.gui.started_at)}</>
                                    )}
                                </p>
                            )}
                            {sysVersion?.deployed?.sha && (
                                <p className="settings-hint">
                                    Latest on disk <code>{sysVersion.deployed.sha}</code>
                                    {sysVersion.deployed.date && (
                                        <> · {new Date(sysVersion.deployed.date).toLocaleString()}</>
                                    )}
                                </p>
                            )}

                            <label className="form-label" style={{ marginTop: '1rem' }}>Automatic Updates</label>
                            <p className="settings-hint">
                                When on, Chad checks <code>master</code> hourly and updates itself the same way
                                the button below does — keeping the always-on server current without you.
                            </p>
                            <button
                                type="button"
                                className="settings-update-btn"
                                onClick={handleAutoUpdateToggle}
                                disabled={autoUpdate === null}
                                aria-pressed={!!autoUpdate}
                            >
                                {autoUpdate === null ? 'Loading…' : autoUpdate ? 'Auto-update: On' : 'Auto-update: Off'}
                            </button>

                            <label className="form-label" style={{ marginTop: '1rem' }}>Production Update</label>
                            <p className="settings-hint">
                                Pulls the latest <code>master</code>, reinstalls changed dependencies,
                                rebuilds the UI if needed, and always restarts the Brain and this
                                window so running code matches disk — no terminal required.
                            </p>
                            <button
                                type="button"
                                className="settings-update-btn"
                                onClick={handleUpdate}
                                disabled={updating}
                            >
                                <RefreshCw size={16} className={updating ? 'is-spinning' : ''} />
                                {updating ? 'Updating…' : 'Update Chad'}
                            </button>
                            {updateResult && <p className="settings-hint settings-update-result">{updateResult}</p>}
                                </>
                            )}
                        </div>
                    )}

                    {visitedCategories.has('services') && (
                        <div style={{ display: category === 'services' ? undefined : 'none' }}>
                        <div className="form-group">
                            <label className="form-label">Default Search</label>
                            <p className="settings-hint">
                                SearXNG is free/self-hosted but can soft-degrade under heavy use
                                (see docs/features/WEB_SEARCH.md). "All API keys" skips SearXNG
                                entirely and rotates whichever of Serper/Tavily/Brave you've
                                configured, so no single key's quota gets exhausted first — set
                                the corresponding *_API_KEY in .env for the ones you want in the
                                rotation. The same backend also backs <code>search_images</code>{' '}
                                fallbacks (SearXNG/Brave/Serper image mode). Openverse (CC images,
                                no key) is listed under Infrastructure Health below; optional
                                Pexels/Unsplash/Pixabay keys are under Connectors → Stock Images.
                            </p>
                            <Dropdown
                                value={searchBackend}
                                options={searchBackendOptions}
                                onChange={handleSearchBackendChange}
                                disabled={searchBackendSaving}
                            />
                            {searchQuota && (
                                <div className="services-grid" style={{ marginTop: '0.75rem' }}>
                                    {(['tavily', 'serper', 'brave'] as const)
                                        .filter((id) => searchQuota[id]?.configured)
                                        .map((id) => {
                                             const q = searchQuota[id];
                                             return (
                                                 <div key={id} className="plan-card services-card">
                                                     <div className="services-card__header">
                                                         <span className="services-card__name" style={{ textTransform: 'capitalize' }}>
                                                             {id}
                                                         </span>
                                                     </div>
                                                     {q.available ? (
                                                         <p className="settings-hint services-card__detail">
                                                             {q.remaining ?? '?'} / {q.limit ?? '?'} remaining
                                                             {q.plan ? ` (${q.plan})` : ''}
                                                         </p>
                                                     ) : (
                                                         <p className="settings-hint services-card__detail">
                                                             {q.error ?? q.note ?? 'No live usage API for this backend.'}
                                                         </p>
                                                     )}
                                                 </div>
                                             );
                                         })}
                                </div>
                            )}
                            {searchQuota && Object.values(searchQuota).every((q) => !q.configured) && (
                                <p className="settings-hint">
                                    No optional search API keys configured — set SERPER_API_KEY /
                                    TAVILY_API_KEY / BRAVE_API_KEY in .env to see usage here.
                                </p>
                            )}
                            <button
                                type="button"
                                className="settings-update-btn settings-update-btn--secondary"
                                onClick={loadSearchQuota}
                                disabled={searchQuotaLoading}
                                style={{ marginTop: '0.5rem' }}
                            >
                                <RefreshCw size={14} className={searchQuotaLoading ? 'is-spinning' : ''} />
                                {searchQuotaLoading ? 'Checking…' : 'Refresh quota'}
                            </button>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Presenton</label>
                            <PresentonSettingsTab
                                onSaved={loadServicesHealth}
                                connected={services?.find((svc) => svc.id === 'presenton')?.healthy}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Chad Code</label>
                            <p className="settings-hint">
                                Delegate real coding tasks to a local model via the OpenHands harness
                                (Docker + LM Studio). Check prerequisites, tune the coder model, and
                                inspect dispatched jobs.
                            </p>
                            <ChadCodeTab />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Infrastructure Health</label>
                            <p className="settings-hint">
                                <strong>Core</strong> services break Chad if down; <strong>Enhanced</strong> ones
                                degrade to a fallback; <strong>Optional</strong> add-ons like Presenton.
                            </p>
                            {servicesError && (
                                <p className="settings-hint settings-update-result">{servicesError}</p>
                            )}
                            {servicesLoading && !services && (
                                <p className="settings-hint">Checking services…</p>
                            )}
                            <div className="services-grid">
                                {(services ?? []).map((svc) => (
                                    <div key={svc.id} className="plan-card services-card">
                                        <div className="services-card__header">
                                            <span className={`services-status-dot ${svc.healthy ? 'services-status-dot--healthy' : 'services-status-dot--unhealthy'}`} />
                                            <span className="services-card__name">{svc.label}</span>
                                            <span className="settings-hint">
                                                {svc.healthy ? 'Connected' : 'Not connected'}
                                            </span>
                                            <span className={`services-tier-badge services-tier-badge--${svc.tier}`}>
                                                {svc.tier === 'core' ? 'Core' : svc.tier === 'optional' ? 'Optional' : 'Enhanced'}
                                            </span>
                                            {svc.mode && (
                                                <span className={`services-mode-badge services-mode-badge--${svc.mode}`}>
                                                    {svc.mode === 'local' ? 'Local (SQLite)' : 'Cloud (Supabase)'}
                                                </span>
                                            )}
                                        </div>
                                        {svc.detail && (
                                            <p className="settings-hint services-card__detail">{svc.detail}</p>
                                        )}
                                        {svc.id === 'searxng' && !svc.healthy && (
                                            <>
                                                <p className="settings-hint services-card__detail">
                                                    First checks the configured SearXNG API. If it is already
                                                    connected, nothing is changed. Chad only starts its local Docker
                                                    stack when the configured URL is local and unreachable.
                                                </p>
                                                <button
                                                    type="button"
                                                    className="settings-update-btn settings-update-btn--secondary"
                                                    onClick={handleEnableSearxng}
                                                    disabled={searxngStarting}
                                                    style={{ marginTop: '0.5rem' }}
                                                >
                                                    <RefreshCw size={14} className={searxngStarting ? 'is-spinning' : ''} />
                                                    {searxngStarting ? 'Connecting…' : 'Connect / set up SearXNG'}
                                                </button>
                                            </>
                                        )}
                                    </div>
                                ))}
                            </div>
                            {searxngMessage && (
                                <p className="settings-hint services-card__detail">{searxngMessage}</p>
                            )}
                            <button
                                type="button"
                                className="settings-update-btn settings-update-btn--secondary"
                                onClick={loadServicesHealth}
                                disabled={servicesLoading}
                                style={{ marginTop: '1rem' }}
                            >
                                <RefreshCw size={16} className={servicesLoading ? 'is-spinning' : ''} />
                                {servicesLoading ? 'Checking…' : 'Verify now'}
                            </button>
                        </div>
                        </div>
                    )}

                    {visitedCategories.has('appearance') && (
                        <div className="form-group" style={{ display: category === 'appearance' ? undefined : 'none' }}>
                            <label className="form-label">Accent Color</label>
                            <p className="settings-hint">Sets the highlight color across Chad — pick what feels like yours.</p>
                            <div className="accent-grid">
                                {ACCENT_PRESETS.map((preset) => (
                                    <button
                                        key={preset.id}
                                        type="button"
                                        className={`accent-swatch ${accent === preset.id ? 'is-active' : ''}`}
                                        style={{ background: `rgb(${preset.rgb})` }}
                                        onClick={() => handleAccentChange(preset.id)}
                                        title={preset.label}
                                        aria-label={preset.label}
                                    >
                                        {accent === preset.id && <Check size={16} />}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </motion.div>
    );
}
