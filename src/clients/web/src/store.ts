/**
 * Global Zustand Store for Chad UI
 * Centralizes all state previously spread across window globals
 */
import { create } from 'zustand';
import {
    applyTurnEvent,
    normalizeTurnEvents,
    type ActiveTurn,
} from './reducers/turnEventReducer';
import {
    applyStreamEvent,
    initialTurnState,
    partsToThinkingEvents,
    type TurnState,
} from './protocol/streamReducer';
import type { StreamEvent } from './protocol/streamEvents';
import { buildToolApprovalActions, type ToolApprovalPayload } from './utils/tool-approval';
import { applyToolApprovalFollowUp } from './utils/tool-approval-followup';
import {
    conversationMatches,
    guardConversation,
    isBusyStatus,
    ownsActiveConversation,
} from './session/sessionGuard';
import { readCache, writeCache } from './utils/localCache';
import { extractSourcesFromThoughts, type SourceEntry } from './utils/sources';
import { pickWaitTheaterOpener } from './utils/waitTheater';
import {
    MAX_CHAT_MESSAGES,
    MAX_MESSAGES_WITH_THOUGHTS,
    MAX_THINKING_EVENTS,
    pruneEvent,
} from './utils/eventPruning';

export const INITIAL_CONVERSATIONS_PAGE_SIZE = 50;
export const CONVERSATIONS_PAGE_SIZE = 50;

/** Optimistic/Brain phase copy that must not linger after status returns to ready. */
const STICKY_TURN_PHASE_LABELS = new Set([
    'Processing Prompt...',
    'Model Loading...',
    'Loading Model...',
    'Thinking...',
    'Executing Action...',
]);

export function isStickyTurnPhaseLabel(text: string | null | undefined): boolean {
    return !!text && STICKY_TURN_PHASE_LABELS.has(text);
}

/**
 * Whether the 3s idle status poll must stand down.
 * Only a *running* streamTurn blocks recovery — a finished/orphaned slot (e.g. Brain
 * never finalized after Stop) must not prevent get_raw_state from clearing stuck busy.
 */
export function isLiveTurnBlockingStatusPoll(state: {
    streamTurn: TurnState | null;
    activeTurn: ActiveTurn | null;
    streamingResponse: string | null;
}): boolean {
    return state.streamTurn?.lifecycle === 'running'
        || !!state.activeTurn
        || !!state.streamingResponse;
}

// Types for our app state
export interface TokenUsage {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    reasoning_tokens?: number | null;
    context_window?: number | null;
    speed?: number | null;
    ttft?: number | null;
    stop_reason?: string | null;
    estimated_prompt_tokens?: number | null;
}

export interface Conversation {
    id: string;
    title: string;
    model?: string;
    created_at: string;
    updated_at: string;
    is_pinned?: boolean;
    project_path?: string | null;
    /** Domain agent this thread belongs to (Financial, Fitness, ...). Null = general tier. */
    domain?: string | null;
}

/**
 * A user-created domain agent. `id` is a slug the Brain writes onto memories and
 * knowledge-graph nodes to scope them, so it is stable identity — renaming a category
 * changes `name`, never `id`.
 */
export interface DomainAgent {
    id: string;
    name: string;
    description: string;
    icon: string;
    color: string;
    persona: string;
    sort_order: number;
    created_at: string;
    updated_at: string;
}

export function sortConversations(conversations: Conversation[]): Conversation[] {
    return [...conversations].sort((a, b) => {
        const pinDiff = (b.is_pinned ? 1 : 0) - (a.is_pinned ? 1 : 0);
        if (pinDiff !== 0) return pinDiff;
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
}

export interface Attachment {
    id: string;
    name: string;
    type: 'text' | 'image' | 'document';
    size?: number;
    mediaType?: string;
    content?: string; // Base64 or text preview
}

export interface ChatMessage {
    conversationId?: string | null;
    contentBlocks?: unknown[] | null;
    category?: string;
    thinkingEnabled?: boolean | null;
    domain?: string | null;
    id: string;
    role: 'user' | 'ai';
    text: string;
    timestamp: Date;
    thoughts?: ThinkingEvent[];
    artifacts?: ThinkingEvent[];
    sources?: SourceEntry[];
    attachments?: Attachment[];
    turnDurationMs?: number;
    // Captured live from thinkingStartTime/thinkingEndTime at finalize — committed thought
    // parts carry no per-event timestamps, so history can't recompute this after the fact.
    thinkingDurationMs?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    reasoning_tokens?: number | null;
    context_window?: number | null;
    ttft?: number;
    stop_reason?: string;
    speed?: number;
    // Optional action buttons for approval flows (e.g., file plan approval)
    actions?: {
        type: 'file_plan_approval';
        approvalToken: string;
        resolved?: boolean;  // Set to true after user clicks
        userChoice?: 'approved' | 'rejected';  // The choice user made
    } | {
        // Generic human-in-the-loop tool approval (accept/decline, MCQ, or structured questions).
        // See src/core/orchestration/approval_store.py for the backend half.
        type: 'tool_approval';
        token: string;
        tool_name?: string;
        params?: Record<string, unknown>;
        decisionType: 'accept_decline' | 'choice' | 'questions';
        prompt: string;
        title?: string;
        questions?: {
            question: string;
            header?: string;
            options: { label: string; description?: string }[];
            multiSelect?: boolean;
        }[];
        options?: { id: string; label: string }[];
        resolved?: boolean;
        userChoice?: string;  // 'approved' | 'declined' | chosen option label | JSON answers
    };
    /**
     * Structured clarifying-question reply — rendered as muted question + bold answer
     * on the USER side (Claude-style two-sided exchange).
     */
    clarifyingReply?: {
        questions: {
            question: string;
            header?: string;
            options: { label: string; description?: string }[];
            multiSelect?: boolean;
        }[];
        userChoice: string;
    };
    userFeedback?: 'positive' | 'negative';
    /**
     * Server-side ephemeral snapshot of a turn still streaming (merged at read time
     * by the desktop bridge). Lets a cold load mid-turn show the question + partial
     * answer. An in-progress ASSISTANT snapshot is hidden once a live streamTurn owns
     * the render (dedup); the in-progress USER message always shows.
     */
    in_progress?: boolean;
    /**
     * Outbound delivery for optimistically inserted USER bubbles.
     * `sending` while handoff to the Brain is in flight; `failed` if it never left
     * (retry kept on the bubble). Cleared (`undefined`) once accepted / from history.
     */
    sendStatus?: 'sending' | 'failed';
}

export interface PlanStep {
    id: string;
    description: string;
    tools?: string[];
    status?: 'pending' | 'running' | 'completed' | 'failed';
}

export interface ThinkingEvent {
    browserProgress?: import('./protocol/streamEvents').BrowserProgressPayload;
    type: 'tool_start' | 'tool_end' | 'thought' | 'thinking' | 'reasoning' | 'turn_start' | 'turn_complete' | 'user_speech' | 'autonomous_notification' | 'model_loading' | 'speech' | 'execution_plan' | 'tool_menu' | 'subagent_group' | 'browser_progress';
    /** Server-assigned unique id for idempotent ingestion (replay / double-delivery guard). */
    id?: string;
    /** Monotonic sequence within a turn for ordering diagnostics. */
    sequence?: number;
    correlation_id?: string;
    name?: string;
    provider?: string;
    params?: Record<string, unknown>;
    prompt?: string;
    display_text?: string;
    success?: boolean;
    result?: string;
    text?: string;
    append?: boolean;
    download_token?: string;
    filename?: string;
    youtube_video_id?: string;
    youtube_title?: string;
    /** When true, UI auto-opens the artifact preview panel (verified HTML builds). */
    auto_preview?: boolean;
    /** Inline OpenStreetMap card from show_map. */
    map_view?: {
        title: string;
        center: { lat: number; lon: number };
        zoom: number;
        places: Array<{
            name: string;
            lat: number;
            lon: number;
            category?: string;
            address?: string;
            osm_url?: string;
        }>;
    };
    parent_id?: string;
    agent_name?: string;
    children?: ThinkingEvent[];
    timestamp?: number;
    awaiting_user?: boolean;
    durationMs?: number;
    // execution_plan fields
    plan?: PlanStep[];
    plan_id?: string;
    goal?: string;
    gather_brief?: string;
    // tool_menu (wait-theater carousel)
    tools?: string[];
    shortlist_size?: number;
    selection_path?: string;
}

export type AppStatus =
    | 'initializing'
    | 'reconnecting'
    | 'ready'
    // Standby. main_window.update_status() defaults to state="idle" and pushes it
    // through handleStateUpdate, so this arrives at runtime; it was missing here,
    // which made the `status === 'idle'` check in setStatus a compile error.
    | 'idle'
    | 'listening'
    | 'thinking'
    | 'speaking'
    | 'executing'
    | 'active'
    | 'error'
    | 'model_loading';

/** All tunable per-model generation settings exposed to the UI. */
export type ModelSettings = {
    thinking?: boolean | null;
    reasoning_effort?: 'none' | 'low' | 'medium' | 'high' | null;
    thinking_budget?: number | null;
    top_p?: number | null;
    top_k?: number | null;
    repeat_penalty?: number | null;
};

export type ActiveModelInfo = {
    mode: 'local' | 'cloud';
    name: string;
    provider: string;
    supports_tools: boolean;
    /** True when supports_tools came from a probe/cache, not a listing guess */
    tools_verified?: boolean;
    /** null = model has no explicit thinking config (toggle hidden); true/false = default state for toggle */
    thinking_enabled: boolean | null;
    /** Full per-model generation settings from config.yaml — only populated for local models */
    model_settings?: ModelSettings;
};

export interface LocalModelInfo {
    name: string;
    display_name: string;
    provider: string;
    supports_tools: boolean;
    tools_verified?: boolean;
    size_hint: string;
    base_url: string;
    api_key: string;
}

export interface OpenArtifactTarget {
    token: string;
    filename: string;
    serverUrl?: string | null;
}

export interface FastTrackCommand {
    alias: string;
    label: string;
    icon?: string | null;
}

export interface CapabilitySuggestion {
    id: string;
    kind: 'tool' | 'skill';
    title: string;
    description: string;
    rationale: string;
    confidence: number;
    cluster_size: number;
    status?: string;
    skill_name?: string;
    written_files?: string[];
}

export interface ReflectionLesson {
    id: string;
    kind: 'lesson' | 'question';
    scope: string;
    text: string;
    why?: string;
    evidence?: string;
    confidence: number;
    status: string;
}

const ARTIFACT_PANEL_BREAKPOINT = '(min-width: 900px)';

/**
 * Top-level destinations in the new workspace shell. The icon rail (WorkspaceRail)
 * switches between these; each renders full-pane in the main area instead of as a
 * modal. Adding a future destination = one more entry here + one rail icon, no relayout.
 */
/**
 * Phases of a single "Hey Chad" voice turn. Mirrors VoicePhase in
 * src/shared/voice_state.py — keep the two in sync.
 */
export type VoicePhase =
    | 'armed'
    | 'listening'
    | 'transcribing'
    | 'heard'
    | 'thinking'
    | 'speaking'
    | 'error';

/** A live voice turn, as pushed by window.handleVoiceState. */
export interface VoiceTurn {
    phase: VoicePhase;
    /** What STT understood — the single most useful thing to show. */
    transcript: string | null;
    /** Reply text as it streams, sentence by sentence. */
    reply: string | null;
    error: string | null;
    /** Client-side receipt time, used to auto-dismiss the pill after a turn ends. */
    updatedAt: number;
}

/** Whether voice mode is armed, and if not, why (window.handleVoiceAvailability). */
export interface VoiceAvailability {
    armed: boolean;
    reason: string | null;
    message: string | null;
    remedy: string | null;
    detail: string | null;
}

export type Workspace = 'chat' | 'routines' | 'board' | 'dashboard' | 'automations';

export type ChatTurnMode = 'auto' | 'agent' | 'chat' | 'plan';

interface AppState {
    // Connection & Status
    apiReady: boolean;
    status: AppStatus;
    statusText: string;

    // Audio
    audioLevel: number;
    /** Live "Hey Chad" turn, or null when no voice turn is in flight. */
    voiceTurn: VoiceTurn | null;
    /** null until the Limb reports in — distinct from "reported, not armed". */
    voiceAvailability: VoiceAvailability | null;

    // Chat
    chatMessages: ChatMessage[];
    chatTurnMode: ChatTurnMode;

    // Live turn state (single source for in-progress AI response blocks)
    activeTurn: ActiveTurn | null;
    /** Full audit log of thinking events (notifications, debug). */
    thinkingEvents: ThinkingEvent[];

    /**
     * Phase 2 streaming refactor: the reducer-driven turn state, folded from the
     * typed StreamEvent protocol via applyStreamEvent. Runs PARALLEL to activeTurn
     * for now (populated only when the Limb has CHAD_STREAM_EVENTS on); the render
     * path migrates to this slice incrementally. See docs/features/STREAMING_REFACTOR.md.
     */
    streamTurn: TurnState | null;
    /** Conversation that owns the in-flight streamTurn (scoped buddy / avatar state). */
    streamConversationId: string | null;

    // UI Mode
    uiMode: 'splash' | 'chat_only' | 'split_view';
    /** Active top-level workspace in the icon-rail shell (chat is the default). */
    activeWorkspace: Workspace;
    /** Command palette (Cmd/Ctrl+K) visibility. */
    commandPaletteOpen: boolean;
    isStoryboardPinned: boolean;
    isNavCollapsed: boolean;
    /** Off-canvas nav/workspace drawer visibility on narrow (mobile) viewports. */
    isMobileNavOpen: boolean;
    splitRatio: number; // 0.0 to 1.0 (Sidebar width percentage)

    // Persistent Chat History
    conversations: Conversation[];
    activeConversationId: string | null;
    isHistoryLoading: boolean;
    isChatLoading: boolean;
    isChatSyncing: boolean;
    conversationsOffset: number;
    hasMoreConversations: boolean;

    // Domain agents (Categories)
    domains: DomainAgent[];
    /** Sidebar filter: show only this domain's threads. Null = show everything. */
    selectedDomainId: string | null;
    /** Domain to stamp on the next lazily-created conversation (set by "new chat in X"). */
    pendingDomainId: string | null;

    // Active Model
    activeModel: ActiveModelInfo | null;
    availableCloudModels: { id: string, name: string }[];
    availableLocalModels: LocalModelInfo[];
    isLocalScanning: boolean;

    // Streaming
    streamingResponse: string | null;
    tokenUsage: TokenUsage | null;
    turnStartTime?: number;
    thinkingStartTime?: number | null;
    thinkingEndTime?: number | null;
    /** Transient pre-first-token phase label ("Selecting tools…", "Loading gemma…",
     * "Thinking…") shown in the live working indicator so long gaps don't look frozen. */
    liveStatus?: string | null;
    /** When liveStatus last changed — drives client carousel when backend goes quiet. */
    liveStatusUpdatedAt?: number | null;
    /** Query-tailored wait-theater narrator lines (Phase C). */
    liveNarratorLines?: string[] | null;

    // Artifact viewer (#114)
    openArtifact: OpenArtifactTarget | null;
    artifactViewerUseModal: boolean;

    // Fast-track commands ('/<alias>' chat shortcuts that skip the LLM/planner)
    fastTrackCommands: FastTrackCommand[];

    /** Whether the multi-step planner lane is enabled (config: planner.enabled). */
    plannerEnabled: boolean;

    /** Pending inline tool approval (attached to the next finalized AI bubble). */
    pendingToolApproval: ToolApprovalPayload | null;
    /** When set, the next addChatMessage(ai) with this turn_id is skipped (already committed on approval submit). */
    skipNextAiTurnFinalizeId: string | null;

    // Capability suggestions (Chad's self-improvement side panel)
    capabilitySuggestions: CapabilitySuggestion[];

    // Reflection lessons/questions (Chad's reflective self-learning review queue, #194)
    reflectionLessons: ReflectionLesson[];

    // Actions
    serverUrl: string;
    setApiReady: (ready: boolean) => void;
    setFastTrackCommands: (commands: FastTrackCommand[]) => void;
    setPlannerEnabled: (enabled: boolean) => void;
    setServerUrl: (url: string) => void;
    setStatus: (status: AppStatus) => void;
    setStatusText: (text: string) => void;
    setLiveStatus: (text: string | null) => void;
    setWaitNarratorLines: (lines: string[] | null) => void;
    /** Optimistic wait-theater kickoff on send — instant label + timer before Brain replies. */
    beginTurnWait: () => void;
    setAudioLevel: (level: number) => void;
    setThinkingStartTime: (time: number | null) => void;
    setThinkingEndTime: (time: number | null) => void;
    addChatMessage: (role: 'user' | 'ai', text: string, attachments?: Attachment[], forcedThoughts?: ThinkingEvent[], messageId?: string, metadata?: Record<string, unknown>) => void;
    addChatMessageWithActions: (role: 'user' | 'ai', text: string, actions: ChatMessage['actions'], attachments?: Attachment[], forcedThoughts?: ThinkingEvent[], messageId?: string, metadata?: Record<string, unknown>) => void;
    /** Set or clear outbound delivery status on a USER bubble (`null` = sent / reconciled). */
    setMessageSendStatus: (messageId: string, sendStatus: 'sending' | 'failed' | null) => void;
    /**
     * Send handoff failed: mark the bubble failed and drop optimistic busy/wait UI so
     * the composer recovers (chat-standard soft failure, not a stuck thinking state).
     */
    failOutboundSend: (messageId: string) => void;
    setPendingToolApproval: (payload: ToolApprovalPayload | null) => void;
    attachToolApprovalToLastAIMessage: (payload: ToolApprovalPayload) => void;
    /** Commit paused turn + user answer before resolve_tool_approval wipes streamTurn. */
    prepareToolApprovalFollowUp: (decision: string, displayText?: string) => void;
    resolveMessageAction: (messageId: string, choice: string) => void;
    submitFeedback: (sessionId: string, turnId: string, rating: 'positive' | 'negative') => Promise<void>;
    addThinkingEvent: (event: ThinkingEvent) => void;
    clearThinkingEvents: () => void;
    /**
     * Session isolation (issue #269): drop ALL in-flight turn state — live stream,
     * active turn, streaming buffer, AND any buffered approval/clarifying-question
     * card. Called on every chat switch / new chat so nothing raised in one
     * conversation can survive into another.
     */
    resetLiveTurnState: () => void;
    /** User hit Stop — drop the in-flight typed turn so the send button recovers immediately. */
    abortActiveTurn: () => void;
    /**
     * A turn finished while the user was viewing a DIFFERENT chat. Clear the live
     * turn slot (it's done) without appending to the visible chatMessages or
     * switching the active conversation — the finalized bubble is persisted server
     * side and will appear when the user navigates back and history is refetched.
     */
    finalizeBackgroundTurn: () => void;
    /** Phase 2: fold a batch of typed StreamEvents into streamTurn (parallel path). */
    applyStreamEvents: (events: StreamEvent[]) => void;
    setStreamConversationId: (id: string | null) => void;
    applyVoiceState: (payload: Partial<VoiceTurn> & { phase: VoicePhase }) => void;
    setVoiceAvailability: (availability: VoiceAvailability) => void;
    clearVoiceTurn: () => void;
    setChatTurnMode: (mode: ChatTurnMode) => void;
    setUiMode: (mode: 'splash' | 'chat_only' | 'split_view') => void;
    setActiveWorkspace: (workspace: Workspace) => void;
    setCommandPaletteOpen: (open: boolean) => void;
    setIsStoryboardPinned: (pinned: boolean) => void;
    setIsNavCollapsed: (collapsed: boolean) => void;
    setIsMobileNavOpen: (open: boolean) => void;
    setSplitRatio: (ratio: number) => void;

    // History Actions
    setConversations: (conversations: Conversation[], pageSize?: number) => void;
    addConversations: (conversations: Conversation[], pageSize?: number) => void;
    resetConversations: () => void;
    setActiveConversation: (id: string | null) => void;
    removeConversation: (id: string) => void;
    updateConversation: (id: string, updates: Partial<Conversation>) => void;
    upsertConversation: (conversation: Conversation) => void;

    // Domain Agent Actions
    setDomains: (domains: DomainAgent[]) => void;
    upsertDomain: (domain: DomainAgent) => void;
    removeDomain: (id: string) => void;
    setSelectedDomain: (id: string | null) => void;
    setPendingDomain: (id: string | null) => void;

    setHistoryLoading: (loading: boolean) => void;
    setChatLoading: (loading: boolean) => void;
    setChatSyncing: (syncing: boolean) => void;
    setChatMessages: (messages: ChatMessage[]) => void;
    setActiveModel: (model: ActiveModelInfo) => void;
    /** Merge settings into the current active model — used after saving from ModelSettingsModal */
    updateActiveModelSettings: (settings: ModelSettings) => void;
    setAvailableCloudModels: (models: { id: string, name: string }[]) => void;
    setAvailableLocalModels: (models: LocalModelInfo[]) => void;
    setIsLocalScanning: (scanning: boolean) => void;
    setStreamingResponse: (text: string | null) => void;
    setTokenUsage: (usage: TokenUsage | null) => void;
    openArtifactViewer: (artifact: OpenArtifactTarget) => void;
    closeArtifactViewer: () => void;
    setCapabilitySuggestions: (suggestions: CapabilitySuggestion[]) => void;
    upsertCapabilitySuggestion: (suggestion: CapabilitySuggestion) => void;
    removeCapabilitySuggestion: (id: string) => void;
    setReflectionLessons: (lessons: ReflectionLesson[]) => void;
    upsertReflectionLesson: (lesson: ReflectionLesson) => void;
    removeReflectionLesson: (id: string) => void;
}

export const useAppStore = create<AppState>((set, get) => ({
    // Initial State
    apiReady: false,
    status: 'initializing',
    statusText: 'Connecting...',
    audioLevel: 0,
    voiceTurn: null,
    voiceAvailability: null,
    chatMessages: [],
    chatTurnMode: 'auto',
    activeTurn: null,
    thinkingEvents: [],
    streamTurn: null,
    streamConversationId: null,
    uiMode: 'splash',
    activeWorkspace: 'chat',
    commandPaletteOpen: false,
    isStoryboardPinned: false,
    isNavCollapsed: false,
    isMobileNavOpen: false,
    splitRatio: 0.6, // Default 60/40 split
    serverUrl: 'http://localhost:8767',

    // History Initial State
    conversations: [],
    activeConversationId: null,
    isHistoryLoading: true,
    isChatLoading: false,
    isChatSyncing: false,
    conversationsOffset: 0,
    hasMoreConversations: true,
    domains: [],
    selectedDomainId: null,
    pendingDomainId: null,
    activeModel: null,
    availableCloudModels: readCache<{ id: string, name: string }[]>('cloud_models') || [],
    availableLocalModels: readCache<LocalModelInfo[]>('local_models') || [],
    isLocalScanning: false,
    streamingResponse: null,
    tokenUsage: null,
    turnStartTime: undefined,
    liveStatus: null,
    liveStatusUpdatedAt: null,
    liveNarratorLines: null,
    thinkingStartTime: null,
    thinkingEndTime: null,
    openArtifact: null,
    artifactViewerUseModal: false,
    fastTrackCommands: [],
    plannerEnabled: false,
    capabilitySuggestions: [],
    reflectionLessons: [],
    pendingToolApproval: null as ToolApprovalPayload | null,
    skipNextAiTurnFinalizeId: null as string | null,

    // Actions
    setApiReady: (ready) => set({ apiReady: ready }),
    setFastTrackCommands: (commands) => set({ fastTrackCommands: commands }),
    setPlannerEnabled: (enabled) => set((state) => {
        const next: Partial<AppState> = { plannerEnabled: enabled };
        if (!enabled && state.chatTurnMode === 'plan') {
            next.chatTurnMode = 'auto';
        }
        return next;
    }),

    setCapabilitySuggestions: (suggestions) => set({ capabilitySuggestions: suggestions }),

    upsertCapabilitySuggestion: (suggestion) => set((state) => {
        const exists = state.capabilitySuggestions.some(s => s.id === suggestion.id);
        if (exists) {
            return {
                capabilitySuggestions: state.capabilitySuggestions.map(s =>
                    s.id === suggestion.id ? { ...s, ...suggestion } : s
                ),
            };
        }
        return { capabilitySuggestions: [suggestion, ...state.capabilitySuggestions] };
    }),

    removeCapabilitySuggestion: (id) => set((state) => ({
        capabilitySuggestions: state.capabilitySuggestions.filter(s => s.id !== id),
    })),

    setReflectionLessons: (lessons) => set({ reflectionLessons: lessons }),

    upsertReflectionLesson: (lesson) => set((state) => {
        const exists = state.reflectionLessons.some(l => l.id === lesson.id);
        if (exists) {
            return {
                reflectionLessons: state.reflectionLessons.map(l =>
                    l.id === lesson.id ? { ...l, ...lesson } : l
                ),
            };
        }
        return { reflectionLessons: [lesson, ...state.reflectionLessons] };
    }),

    removeReflectionLesson: (id) => set((state) => ({
        reflectionLessons: state.reflectionLessons.filter(l => l.id !== id),
    })),

    setServerUrl: (url) => set({ serverUrl: url }),

    setStatus: (status) => set((state) => {
        // Drop leftover turn-phase labels when we go idle. Abort sets
        // "Task stopped by user" in the same set() as ready (bypassing this),
        // so that message is preserved. Already-ready + sticky text (the
        // handleStateUpdate('ready') path that used to leave Processing Prompt…
        // forever) is also cleaned here.
        const clearSticky =
            (status === 'ready' || status === 'idle')
            && isStickyTurnPhaseLabel(state.statusText);
        if (status === state.status) {
            return clearSticky ? { statusText: '' } : {};
        }
        return clearSticky ? { status, statusText: '' } : { status };
    }),

    setStatusText: (text) => set({ statusText: text }),

    setLiveStatus: (text) => set({
        liveStatus: text,
        liveStatusUpdatedAt: text ? Date.now() : null,
    }),

    setWaitNarratorLines: (lines) => set({
        liveNarratorLines: lines && lines.length > 0 ? lines : null,
    }),

    beginTurnWait: () => {
        const now = Date.now();
        set({
            liveStatus: pickWaitTheaterOpener(),
            liveStatusUpdatedAt: now,
            liveNarratorLines: null,
            thinkingStartTime: now,
            // Prior-turn telemetry must not drive live speed / ticker mid-gap.
            tokenUsage: null,
        });
    },

    setAudioLevel: (level) => set({ audioLevel: level }),

    setThinkingStartTime: (time) => set({ thinkingStartTime: time }),
    setThinkingEndTime: (time) => set({ thinkingEndTime: time }),

    addChatMessage: (role, text, attachments, forcedThoughts, messageId, metadata) => set((state) => {
        if (
            role === 'ai'
            && messageId
            && state.skipNextAiTurnFinalizeId
            && messageId === state.skipNextAiTurnFinalizeId
        ) {
            return { skipNextAiTurnFinalizeId: null };
        }

        // DEBUG: Log message creation with current state snapshot
        console.log(`[DEBUG addChatMessage] role=${role} text_len=${(text||'').length} activeTurn_count=${state.activeTurn?.events.length ?? 0} forcedThoughts_count=${forcedThoughts?.length ?? 0}`);
        
        const meta = metadata as {
            turn_duration_ms?: number;
            thinking_duration_ms?: number;
            prompt_tokens?: number;
            completion_tokens?: number;
            total_tokens?: number;
            reasoning_tokens?: number | null;
            context_window?: number | null;
            ttft?: number;
            stop_reason?: string;
            speed?: number;
            sendStatus?: 'sending' | 'failed';
        } | null | undefined;

        let turnStartTime = state.turnStartTime;
        let turnDurationMs: number | undefined = undefined;
        let thinkingStartTime = state.thinkingStartTime;
        let thinkingEndTime = state.thinkingEndTime;
        
        if (role === 'user') {
            turnStartTime = Date.now();
            thinkingStartTime = Date.now();
            thinkingEndTime = null;
        } else if (role === 'ai') {
            if (state.turnStartTime) {
                turnDurationMs = Date.now() - state.turnStartTime;
                turnStartTime = undefined; // reset
            }
            // Content may never trip hasContentStarted before finalize (race / text-only
            // commit). Stamp end now so history gets a real thinkingDurationMs.
            if (thinkingStartTime && !thinkingEndTime) {
                thinkingEndTime = Date.now();
            }
        }

        // Prune thoughts being attached to the message to save memory. The live
        // turn's thoughts come from the typed reducer (streamTurn); forcedThoughts
        // still wins when a caller supplies them explicitly.
        const thoughtsSource = forcedThoughts && forcedThoughts.length > 0
            ? forcedThoughts
            : (state.streamTurn ? partsToThinkingEvents(state.streamTurn) : []);
        // Sources must be derived from the raw (pre-prune) thoughtsSource — pruneEvent
        // truncates long tool `result` strings for memory, which would corrupt the
        // JSON-stringified web_search payload before it can be parsed.
        const sources = role === 'ai' ? extractSourcesFromThoughts(thoughtsSource) : undefined;
        const thoughts = role === 'ai'
            ? normalizeTurnEvents(thoughtsSource.map(pruneEvent))
            : undefined;
        console.log(`[DEBUG addChatMessage] Final attached thoughts defined: ${!!thoughts}, size: ${thoughts?.length ?? 0}`);
        const artifacts = thoughts ? thoughts.filter(e => e.type === 'tool_end' && e.success && e.download_token) : undefined;

        // Session isolation (issue #269): only stamp a buffered approval onto this
        // finalized bubble if the card belongs to the conversation on screen. If the
        // user navigated away since it was raised, activeConversationId no longer
        // matches and the card is dropped rather than leaked into the wrong chat.
        const rawPending = role === 'ai' ? state.pendingToolApproval : null;
        const pendingApproval = rawPending && guardConversation(
            rawPending.conversationId, state.activeConversationId, 'attach-approval:addChatMessage',
        ) ? rawPending : null;
        const actions = pendingApproval ? buildToolApprovalActions(pendingApproval) : undefined;

        // Truncate chat history to prevent memory bloat
        let updatedMessages = [
            ...state.chatMessages,
            {
                id: messageId || `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                role,
                text,
                timestamp: new Date(),
                thoughts: thoughts && thoughts.length > 0 ? thoughts : undefined,
                artifacts: artifacts && artifacts.length > 0 ? artifacts : undefined,
                sources: sources && sources.length > 0 ? sources : undefined,
                attachments: attachments && attachments.length > 0 ? attachments : undefined,
                actions,
                turnDurationMs: meta?.turn_duration_ms !== undefined ? meta.turn_duration_ms : turnDurationMs,
                thinkingDurationMs: meta?.thinking_duration_ms !== undefined
                    ? meta.thinking_duration_ms
                    : ((thinkingStartTime && thinkingEndTime) ? thinkingEndTime - thinkingStartTime : undefined),
                prompt_tokens: meta?.prompt_tokens,
                completion_tokens: meta?.completion_tokens,
                total_tokens: meta?.total_tokens,
                reasoning_tokens: meta?.reasoning_tokens,
                context_window: meta?.context_window,
                ttft: meta?.ttft,
                stop_reason: meta?.stop_reason,
                speed: meta?.speed,
                ...(role === 'user' && meta?.sendStatus ? { sendStatus: meta.sendStatus } : {}),
            },
        ];

        if (updatedMessages.length > MAX_CHAT_MESSAGES) {
            updatedMessages = updatedMessages.slice(-MAX_CHAT_MESSAGES);
        }

        // Deep Prune: Clear thoughts from messages older than MAX_MESSAGES_WITH_THOUGHTS
        // This is the most effective way to prevent 2GB+ memory bloat in long sessions
        if (updatedMessages.length > MAX_MESSAGES_WITH_THOUGHTS) {
            const cutOff = updatedMessages.length - MAX_MESSAGES_WITH_THOUGHTS;
            for (let i = 0; i < cutOff; i++) {
                if (updatedMessages[i].thoughts) {
                    updatedMessages[i] = { ...updatedMessages[i], thoughts: undefined };
                }
            }
        }

        // Finalizing an AI message ends the live turn: clear reducer slot + timers so
        // they cannot bleed into the next send, and flip busy → ready locally (Brain's
        // ready StatusUpdate can race or miss; idle poll alone used to be blocked by a
        // leftover streamTurn).
        const aiEndTurn = role === 'ai';
        return {
            chatMessages: updatedMessages,
            activeTurn: aiEndTurn ? null : state.activeTurn,
            streamTurn: aiEndTurn ? null : state.streamTurn,
            streamConversationId: aiEndTurn ? null : state.streamConversationId,
            streamingResponse: null, // Clear streaming buffer when message is finalized
            // The turn is over: drop any buffered approval (matched or not) so a stale
            // card can never carry into a later turn or a different chat.
            pendingToolApproval: aiEndTurn && rawPending ? null : state.pendingToolApproval,
            turnStartTime,
            thinkingStartTime: aiEndTurn ? null : thinkingStartTime,
            thinkingEndTime: aiEndTurn ? null : thinkingEndTime,
            liveStatus: null, // phase label is only meaningful pre-first-token
            liveStatusUpdatedAt: null,
            liveNarratorLines: null,
            ...(aiEndTurn
                && (state.status === 'thinking' || state.status === 'executing' || state.status === 'model_loading')
                ? {
                    status: 'ready' as AppStatus,
                    ...(isStickyTurnPhaseLabel(state.statusText) ? { statusText: '' } : {}),
                }
                : {}),
        };
    }),

    setMessageSendStatus: (messageId, sendStatus) => set((state) => ({
        chatMessages: state.chatMessages.map((msg) => {
            if (msg.id !== messageId) return msg;
            if (sendStatus == null) {
                if (!msg.sendStatus) return msg;
                const rest = { ...msg };
                delete rest.sendStatus;
                return rest;
            }
            return { ...msg, sendStatus };
        }),
    })),

    failOutboundSend: (messageId) => set((state) => ({
        chatMessages: state.chatMessages.map((msg) =>
            msg.id === messageId ? { ...msg, sendStatus: 'failed' as const } : msg
        ),
        status: 'ready' as AppStatus,
        statusText: '',
        liveStatus: null,
        liveStatusUpdatedAt: null,
        liveNarratorLines: null,
        thinkingStartTime: null,
        thinkingEndTime: null,
        tokenUsage: null,
        // Handoff never reached the Brain — drop the optimistic live-turn ownership.
        ...(state.streamTurn ? {} : { streamConversationId: null }),
    })),

    setPendingToolApproval: (payload) => set({ pendingToolApproval: payload }),

    attachToolApprovalToLastAIMessage: (payload) => set((state) => {
        // Session isolation (issue #269): never stamp an approval onto a chat it does
        // not belong to. If the card is for a different conversation than the one on
        // screen, drop it — the turn that raised it is no longer visible.
        if (!guardConversation(payload.conversationId, state.activeConversationId, 'attach-approval:direct')) {
            return {};
        }
        const actions = buildToolApprovalActions(payload);
        const messages = [...state.chatMessages];
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'ai') {
                messages[i] = { ...messages[i], actions };
                return { chatMessages: messages, pendingToolApproval: null };
            }
        }
        return { pendingToolApproval: payload };
    }),

    prepareToolApprovalFollowUp: (decision, displayText) => set((state) =>
        applyToolApprovalFollowUp(state, decision, displayText),
    ),

    addChatMessageWithActions: (role, text, actions, attachments, forcedThoughts, messageId, metadata) => set((state) => {
        const meta = metadata as {
            turn_duration_ms?: number;
            thinking_duration_ms?: number;
            prompt_tokens?: number;
            completion_tokens?: number;
            total_tokens?: number;
            reasoning_tokens?: number | null;
            context_window?: number | null;
            ttft?: number;
            stop_reason?: string;
            speed?: number;
        } | null | undefined;

        let turnStartTime = state.turnStartTime;
        let turnDurationMs: number | undefined = undefined;
        let thinkingStartTime = state.thinkingStartTime;
        let thinkingEndTime = state.thinkingEndTime;
        
        if (role === 'user') {
            turnStartTime = Date.now();
            thinkingStartTime = Date.now();
            thinkingEndTime = null;
        } else if (role === 'ai') {
            if (state.turnStartTime) {
                turnDurationMs = Date.now() - state.turnStartTime;
                turnStartTime = undefined; // reset
            }
            if (thinkingStartTime && !thinkingEndTime) {
                thinkingEndTime = Date.now();
            }
        }
        const thoughtsSource = forcedThoughts && forcedThoughts.length > 0
            ? forcedThoughts
            : (state.streamTurn ? partsToThinkingEvents(state.streamTurn) : []);
        const sources = role === 'ai' ? extractSourcesFromThoughts(thoughtsSource) : undefined;
        const thoughts = role === 'ai'
            ? normalizeTurnEvents(thoughtsSource.map(pruneEvent))
            : undefined;
        const artifacts = thoughts ? thoughts.filter(e => e.type === 'tool_end' && e.success && e.download_token) : undefined;
        let updatedMessages = [
            ...state.chatMessages,
            {
                id: messageId || `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                role,
                text,
                timestamp: new Date(),
                thoughts: thoughts && thoughts.length > 0 ? thoughts : undefined,
                artifacts: artifacts && artifacts.length > 0 ? artifacts : undefined,
                sources: sources && sources.length > 0 ? sources : undefined,
                attachments: attachments && attachments.length > 0 ? attachments : undefined,
                actions,
                turnDurationMs: meta?.turn_duration_ms !== undefined ? meta.turn_duration_ms : turnDurationMs,
                thinkingDurationMs: meta?.thinking_duration_ms !== undefined
                    ? meta.thinking_duration_ms
                    : ((thinkingStartTime && thinkingEndTime) ? thinkingEndTime - thinkingStartTime : undefined),
                prompt_tokens: meta?.prompt_tokens,
                completion_tokens: meta?.completion_tokens,
                total_tokens: meta?.total_tokens,
                reasoning_tokens: meta?.reasoning_tokens,
                context_window: meta?.context_window,
                ttft: meta?.ttft,
                stop_reason: meta?.stop_reason,
                speed: meta?.speed,
            },
        ];

        if (updatedMessages.length > MAX_CHAT_MESSAGES) {
            updatedMessages = updatedMessages.slice(-MAX_CHAT_MESSAGES);
        }

        if (updatedMessages.length > MAX_MESSAGES_WITH_THOUGHTS) {
            const cutOff = updatedMessages.length - MAX_MESSAGES_WITH_THOUGHTS;
            for (let i = 0; i < cutOff; i++) {
                if (updatedMessages[i].thoughts) {
                    updatedMessages[i] = { ...updatedMessages[i], thoughts: undefined };
                }
            }
        }

        const aiEndTurn = role === 'ai';
        return {
            chatMessages: updatedMessages,
            activeTurn: aiEndTurn ? null : state.activeTurn,
            streamTurn: aiEndTurn ? null : state.streamTurn,
            streamConversationId: aiEndTurn ? null : state.streamConversationId,
            turnStartTime,
            thinkingStartTime: aiEndTurn ? null : thinkingStartTime,
            thinkingEndTime: aiEndTurn ? null : thinkingEndTime,
            liveStatus: null,
            liveStatusUpdatedAt: null,
            liveNarratorLines: null,
            ...(aiEndTurn
                && (state.status === 'thinking' || state.status === 'executing' || state.status === 'model_loading')
                ? {
                    status: 'ready' as AppStatus,
                    ...(isStickyTurnPhaseLabel(state.statusText) ? { statusText: '' } : {}),
                }
                : {}),
        };
    }),

    resolveMessageAction: (messageId, choice) => set((state) => ({
        chatMessages: state.chatMessages.map((msg) =>
            msg.id === messageId && msg.actions
                ? { ...msg, actions: { ...msg.actions, resolved: true, userChoice: choice } as ChatMessage['actions'] }
                : msg
        ),
    })),

    submitFeedback: async (sessionId, turnId, rating) => {
        const { serverUrl } = get();
        try {
            const response = await fetch(`${serverUrl}/api/sessions/${sessionId}/turns/${turnId}/feedback`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ rating }),
            });
            if (!response.ok) {
                console.error('Failed to submit feedback:', await response.text());
                return;
            }
            // Update local state
            set((state) => ({
                chatMessages: state.chatMessages.map((msg) =>
                    msg.id === turnId ? { ...msg, userFeedback: rating } : msg
                ),
            }));
        } catch (error) {
            console.error('Error submitting feedback:', error);
        }
    },

    addThinkingEvent: (event) => set((state) => {
        const prunedEvent: ThinkingEvent = {
            ...pruneEvent(event),
            timestamp: event.timestamp || Date.now(),
        };

        const { activeTurn, skipped } = applyTurnEvent(state.activeTurn, prunedEvent);
        if (skipped) {
            return state;
        }

        const mergeable = ['thought', 'thinking', 'reasoning', 'speech'] as const;
        let updatedFull = [...state.thinkingEvents];
        const lastFull = updatedFull[updatedFull.length - 1];
        const isMergeable = mergeable.includes(prunedEvent.type as typeof mergeable[number]);
        if (isMergeable && lastFull && lastFull.type === prunedEvent.type) {
            const shouldAppend =
                prunedEvent.append || prunedEvent.type === 'reasoning' || prunedEvent.type === 'thought';
            updatedFull[updatedFull.length - 1] = {
                ...lastFull,
                text: shouldAppend
                    ? (lastFull.text || '') + (prunedEvent.text || '')
                    : prunedEvent.text,
            };
        } else if (prunedEvent.type === 'execution_plan') {
            const fullIdx = updatedFull.findIndex((e) => e.type === 'execution_plan');
            if (fullIdx !== -1) {
                const existing = updatedFull[fullIdx];
                updatedFull[fullIdx] = {
                    ...prunedEvent,
                    plan_id: prunedEvent.plan_id ?? existing.plan_id,
                    goal: prunedEvent.goal ?? existing.goal,
                };
            } else {
                updatedFull.push(prunedEvent);
            }
        } else {
            updatedFull.push(prunedEvent);
        }

        if (updatedFull.length > MAX_THINKING_EVENTS) {
            updatedFull = updatedFull.slice(-MAX_THINKING_EVENTS);
        }

        const patch: Partial<AppState> = {
            activeTurn,
            thinkingEvents: updatedFull,
        };

        if (prunedEvent.type === 'model_loading') {
            // Drive the Loading… banner / local display name, but never wipe real
            // settings (thinking toggle, generation knobs) that came from get_active_model_info.
            const prev = state.activeModel;
            patch.activeModel = {
                ...(prev ?? {
                    supports_tools: true,
                    thinking_enabled: null as boolean | null,
                    model_settings: {},
                }),
                mode: 'local',
                name: prunedEvent.name || prev?.name || 'AI Model',
                provider: prunedEvent.provider || prev?.provider || 'Assistant',
            };
        }

        return patch;
    }),

    clearThinkingEvents: () => set({ thinkingEvents: [], activeTurn: null, streamTurn: null, streamConversationId: null }),

    resetLiveTurnState: () => set((state) => ({
        activeTurn: null,
        streamTurn: null,
        streamConversationId: null,
        streamingResponse: null,
        pendingToolApproval: null,
        liveStatus: null,
        liveStatusUpdatedAt: null,
        liveNarratorLines: null,
        ...(isBusyStatus(state.status)
            ? {
                status: 'ready' as AppStatus,
                statusText: '',
                thinkingStartTime: null,
                thinkingEndTime: null,
            }
            : {}),
    })),

    abortActiveTurn: () => set((state) => {
        const turnActive = state.streamTurn?.lifecycle === 'running'
            || !!state.activeTurn
            || !!state.streamingResponse
            || ['thinking', 'speaking', 'executing', 'model_loading'].includes(state.status);
        if (!turnActive) {
            return state.status === 'ready' ? state : {
                status: 'ready' as AppStatus,
                liveStatus: null,
                liveStatusUpdatedAt: null,
                liveNarratorLines: null,
            };
        }
        // Only flip the send-button/status state — do NOT clear streamTurn.
        // The backend flushes its buffer then emits run_finished(stop_reason='user_cancelled')
        // via the normal push_stream_event path, setting lifecycle=finished.
        // push_ai_response then fires addAIMessageToChat → addChatMessage reads the
        // now-finished streamTurn for thoughts, commits the bubble, and clears streamTurn.
        // This is the identical code path as a normal turn end — no special cases needed.
        return {
            activeTurn: null,
            streamingResponse: null,
            liveStatus: null,
            liveStatusUpdatedAt: null,
            liveNarratorLines: null,
            status: 'ready' as AppStatus,
            statusText: 'Task stopped by user',
            thinkingStartTime: null,
            thinkingEndTime: null,
        };
    }),

    finalizeBackgroundTurn: () => set((state) => ({
        activeTurn: null,
        streamTurn: null,
        streamConversationId: null,
        streamingResponse: null,
        pendingToolApproval: null,
        // Global live-status is only meaningful for the on-screen chat; a background
        // finish returns the shared status to idle (the visible chat's busy state is
        // scoped by getActiveChatStatus, so this can't wrongly un-busy the viewed chat).
        status: isBusyStatus(state.status) ? ('ready' as AppStatus) : state.status,
        statusText: '',
        liveStatus: null,
        liveStatusUpdatedAt: null,
        liveNarratorLines: null,
        thinkingStartTime: null,
        thinkingEndTime: null,
        turnStartTime: undefined,
    })),

    applyStreamEvents: (events) => set((state) => {
        if (!events || events.length === 0) return state;
        // Fold events into the running turn's streamTurn regardless of which chat is
        // currently on screen. There is one in-flight turn at a time (single active
        // Brain websocket); its owner is streamConversationId. Rendering is gated
        // separately (getActiveChatStreamTurn only returns it when owner === active),
        // so a backgrounded turn keeps ACCUMULATING here instead of being dropped when
        // the user navigates away — that dropping was the "switch away, come back,
        // nothing there" bug. Isolation is preserved at render time, not by discarding.
        // run_started resets the turn; otherwise fold onto the existing streamTurn.
        let next = state.streamTurn ?? initialTurnState();
        for (const event of events) {
            next = applyStreamEvent(next, event);
        }
        return { streamTurn: next };
    }),

    setStreamConversationId: (id) => set({ streamConversationId: id }),

    /**
     * Fold one voice phase transition into the live turn.
     *
     * 'armed' is the idle state between turns, so it clears rather than renders.
     * 'listening' starts a fresh turn (wake fired). Everything in between merges,
     * so the transcript captured at 'heard' stays visible through 'thinking' and
     * 'speaking' instead of flashing past.
     */
    applyVoiceState: (payload) => set((state) => {
        if (payload.phase === 'armed') return { voiceTurn: null };

        const isNewTurn = payload.phase === 'listening' || !state.voiceTurn;
        const prev = isNewTurn ? null : state.voiceTurn;

        // Reply arrives one spoken sentence at a time — accumulate so the pill
        // shows the whole utterance rather than only the last fragment.
        const reply = payload.reply
            ? [prev?.reply, payload.reply].filter(Boolean).join(' ')
            : prev?.reply ?? null;

        return {
            voiceTurn: {
                phase: payload.phase,
                transcript: payload.transcript ?? prev?.transcript ?? null,
                reply,
                error: payload.error ?? null,
                updatedAt: Date.now(),
            },
        };
    }),

    setVoiceAvailability: (availability) => set({ voiceAvailability: availability }),

    clearVoiceTurn: () => set({ voiceTurn: null }),

    setChatTurnMode: (mode) => set((state) => {
        if (mode === 'plan' && !state.plannerEnabled) {
            return { chatTurnMode: 'auto' };
        }
        return { chatTurnMode: mode };
    }),

    setUiMode: (mode) => set({ uiMode: mode }),
    setActiveWorkspace: (workspace) => set({ activeWorkspace: workspace }),
    setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),

    setIsStoryboardPinned: (pinned) => set({ isStoryboardPinned: pinned }),
    setIsNavCollapsed: (collapsed) => set({ isNavCollapsed: collapsed }),
    setIsMobileNavOpen: (open) => set({ isMobileNavOpen: open }),
    setSplitRatio: (ratio) => set({ splitRatio: ratio }),

    // History Actions
    setConversations: (conversations, pageSize = INITIAL_CONVERSATIONS_PAGE_SIZE) => set({ 
        conversations: sortConversations(conversations), 
        conversationsOffset: conversations.length,
        hasMoreConversations: conversations.length === pageSize 
    }),

    addConversations: (newConvs, pageSize = CONVERSATIONS_PAGE_SIZE) => set((state) => ({
        conversations: sortConversations([...state.conversations, ...newConvs]),
        conversationsOffset: state.conversationsOffset + newConvs.length,
        hasMoreConversations: newConvs.length === pageSize
    })),

    resetConversations: () => set({
        conversations: [],
        conversationsOffset: 0,
        hasMoreConversations: true
    }),
    
    setActiveConversation: (id) => set({ activeConversationId: id }),
    
    removeConversation: (id) => set((state) => ({
        conversations: state.conversations.filter(c => c.id !== id),
        activeConversationId: state.activeConversationId === id ? null : state.activeConversationId,
        chatMessages: state.activeConversationId === id ? [] : state.chatMessages,
        activeTurn: state.activeConversationId === id ? null : state.activeTurn,
    })),

    updateConversation: (id, updates) => set((state) => ({
        conversations: sortConversations(
            state.conversations.map(c => (c.id === id ? { ...c, ...updates } : c))
        ),
    })),
    
    upsertConversation: (conv) => set((state) => {
        const id = conv.id;
        const index = state.conversations.findIndex(c => c.id === id);
        let newConvs;
        if (index !== -1) {
            newConvs = [...state.conversations];
            newConvs[index] = { ...newConvs[index], ...conv };
        } else {
            newConvs = [conv, ...state.conversations];
        }
        
        return {
            conversations: sortConversations(newConvs),
        };
    }),

    setDomains: (domains) => set({ domains }),

    upsertDomain: (domain) => set((state) => {
        const index = state.domains.findIndex(d => d.id === domain.id);
        if (index === -1) return { domains: [...state.domains, domain] };
        const next = [...state.domains];
        next[index] = { ...next[index], ...domain };
        return { domains: next };
    }),

    removeDomain: (id) => set((state) => ({
        domains: state.domains.filter(d => d.id !== id),
        // Don't strand the user inside a filter for a category that no longer exists.
        selectedDomainId: state.selectedDomainId === id ? null : state.selectedDomainId,
        pendingDomainId: state.pendingDomainId === id ? null : state.pendingDomainId,
    })),

    setSelectedDomain: (id) => set({ selectedDomainId: id }),
    setPendingDomain: (id) => set({ pendingDomainId: id }),

    setHistoryLoading: (loading) => set({ isHistoryLoading: loading }),
    setChatLoading: (loading) => set({ isChatLoading: loading }),
    setChatSyncing: (syncing) => set({ isChatSyncing: syncing }),

    setChatMessages: (messages) => set(() => {
        const sliced = messages.slice(-MAX_CHAT_MESSAGES);
        const pruned = sliced.map((msg, idx) => {
            const isRecent = idx >= sliced.length - MAX_MESSAGES_WITH_THOUGHTS;
            return {
                ...msg,
                thoughts: isRecent && msg.thoughts
                    ? normalizeTurnEvents(msg.thoughts.map(pruneEvent))
                    : undefined
            };
        });
        return { chatMessages: pruned };
    }),

    setActiveModel: (model) => set({ activeModel: model }),
    updateActiveModelSettings: (settings) => set((state) => {
        if (!state.activeModel) return {};
        const merged: ModelSettings = { ...(state.activeModel.model_settings ?? {}), ...settings };
        // Remove null/undefined keys
        (Object.keys(merged) as (keyof ModelSettings)[]).forEach(k => {
            if (merged[k] == null) delete merged[k];
        });
        const thinking_enabled = 'thinking' in merged
            ? (merged.thinking != null ? Boolean(merged.thinking) : null)
            : state.activeModel.thinking_enabled;
        return {
            activeModel: {
                ...state.activeModel,
                model_settings: merged,
                thinking_enabled,
            },
        };
    }),
    setAvailableCloudModels: (models) => {
        writeCache('cloud_models', models);
        set({ availableCloudModels: models });
    },
    setAvailableLocalModels: (models) => {
        writeCache('local_models', models);
        set({ availableLocalModels: models });
    },
    setIsLocalScanning: (scanning) => set({ isLocalScanning: scanning }),
    setStreamingResponse: (text) => set({ streamingResponse: text }),
    setTokenUsage: (usage) => set({ tokenUsage: usage }),

    openArtifactViewer: (artifact) =>
        set({
            openArtifact: artifact,
            artifactViewerUseModal: !window.matchMedia(ARTIFACT_PANEL_BREAKPOINT).matches,
        }),

    closeArtifactViewer: () =>
        set({ openArtifact: null, artifactViewerUseModal: false }),
}));

// Selector hooks for optimized re-renders
export const useStatus = () => useAppStore((state) => state.status);
export const useStatusText = () => useAppStore((state) => state.statusText);
export const useApiReady = () => useAppStore((state) => state.apiReady);
export const useChatMessages = () => useAppStore((state) => state.chatMessages);
export const useIsChatSyncing = () => useAppStore((state) => state.isChatSyncing);
export const useAudioLevel = () => useAppStore((state) => state.audioLevel);
export const useVoiceTurn = () => useAppStore((state) => state.voiceTurn);
export const useVoiceAvailability = () => useAppStore((state) => state.voiceAvailability);
export const useUiMode = () => useAppStore((state) => state.uiMode);
export const useActiveWorkspace = () => useAppStore((state) => state.activeWorkspace);
export const useActiveModel = () => useAppStore((state) => state.activeModel);
/** Stable fallback — must not allocate a new [] per selector call (infinite re-render loop). */
const EMPTY_TURN_EVENTS: ThinkingEvent[] = [];

/** Live in-progress turn blocks — single render source for the active AI bubble. */
export const useActiveTurnEvents = () =>
    useAppStore((state) => state.activeTurn?.events ?? EMPTY_TURN_EVENTS);
export const useChatTurnMode = () => useAppStore((state) => state.chatTurnMode);
export const usePlannerEnabled = () => useAppStore((state) => state.plannerEnabled);

type LiveTurnScope = Pick<AppState, 'streamTurn' | 'streamConversationId' | 'activeConversationId' | 'status'>;

export function getActiveChatStreamTurn(state: LiveTurnScope): TurnState | null {
    return ownsActiveConversation(state.streamConversationId, state.activeConversationId)
        ? state.streamTurn
        : null;
}

export function getActiveChatBusy(state: LiveTurnScope): boolean {
    if (!ownsActiveConversation(state.streamConversationId, state.activeConversationId)) {
        return false;
    }
    return state.streamTurn?.lifecycle === 'running' || isBusyStatus(state.status);
}

export function getActiveChatStatus(state: LiveTurnScope): AppStatus {
    return ownsActiveConversation(state.streamConversationId, state.activeConversationId)
        ? state.status
        : 'ready';
}

/** The live reducer turn only when it belongs to the chat on screen. */
export const useActiveChatStreamTurn = () =>
    useAppStore((state) => getActiveChatStreamTurn(state));

/** Busy state for controls/rendering, scoped to the active chat session. */
export const useActiveChatBusy = () =>
    useAppStore((state) => getActiveChatBusy(state));

export const useActiveChatStatus = () =>
    useAppStore((state) => getActiveChatStatus(state));

export const useActiveChatPendingToolApproval = () =>
    useAppStore((state) => {
        const approval = state.pendingToolApproval;
        if (!approval) return null;
        return conversationMatches(approval.conversationId, state.activeConversationId)
            ? approval
            : null;
    });

/** True while the active chat's turn is streaming (run_started → run_finished). */
export const useTurnRunning = () =>
    useAppStore((state) => getActiveChatStreamTurn(state)?.lifecycle === 'running');
/** @deprecated Use useActiveTurnEvents — kept as alias during migration. */
export const useCurrentThoughts = () => useActiveTurnEvents();

export type { ActiveTurn } from './reducers/turnEventReducer';
