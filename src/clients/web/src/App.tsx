import { useEffect, useState, useRef, lazy, Suspense } from 'react';
import { AnimatePresence } from 'framer-motion';
import { Menu } from 'lucide-react';
import { useAuth } from './hooks/useAuth';
import { isBrowserTransport, isLocalOrigin } from './transport/pywebviewShim';
import { isSupabaseConfigured } from './services/supabaseClient';
import { hasCredentials, setToken } from './transport/transportConfig';
import { brainSocket } from './transport/brainSocket';
const LoginScreen = lazy(() => import('./components/auth/LoginScreen'));
const SetupWizard = lazy(() => import('./components/setup/SetupWizard'));
import StartupScreen from './components/setup/StartupScreen';
import {
  installStreamHandlers,
  extractTextFromContent,
} from './runtime/streamRuntime';
import { normalizeTurnEvent } from './protocol/turnProtocol';
import { useAppStore, useApiReady, useStatusText, INITIAL_CONVERSATIONS_PAGE_SIZE, CONVERSATIONS_PAGE_SIZE, isLiveTurnBlockingStatusPoll } from './store'
import type { AppStatus, ThinkingEvent, Conversation, TokenUsage, ActiveModelInfo, Attachment, FastTrackCommand, CapabilitySuggestion, ReflectionLesson, ChatMessage, VoicePhase, VoiceAvailability } from './store'
import type { StreamEvent } from './protocol/streamEvents'
import type { KanbanCard } from './components/workspaces/kanban.types'
import AppShell from './components/layout/AppShell'
import BrowserConnectModal from './components/modals/BrowserConnectModal'
import NavSidebar from './components/layout/NavSidebar'
import WorkspaceRail from './components/layout/WorkspaceRail'
import CommandPalette from './components/common/CommandPalette'
import { initAccent } from './utils/accent'
import { readCache } from './utils/localCache'
import { extractSourcesFromThoughts } from './utils/sources'
import ChatPanel from './components/layout/ChatPanel'
import ArtifactSidePanel from './components/layout/ArtifactSidePanel'
import ArtifactViewerModal from './components/data-display/ArtifactViewerModal'
import ControlDock from './components/layout/ControlDock'
import ConnectionBanner from './components/layout/ConnectionBanner'
import ChatStream from './components/layout/ChatStream'
import ChatSkeleton from './components/layout/ChatSkeleton'
import ChatInput from './components/layout/ChatInput'
import SuggestedActions from './components/layout/SuggestedActions'
import { ChadAvatarLive } from './components/common/chad-avatar'
import type { AttachedFile } from './components/layout/ChatInput'
import NotificationOverlay from './components/NotificationOverlay'
import VoiceOverlay from './components/voice/VoiceOverlay'
import PushBanner from './components/common/PushBanner'
import SettingsView from './components/settings/SettingsView'
import RoutinesWorkspace from './components/workspaces/RoutinesWorkspace'
import BoardWorkspace from './components/workspaces/BoardWorkspace'
import DashboardWorkspace from './components/workspaces/DashboardWorkspace'
import AutomationsWorkspace from './components/workspaces/AutomationsWorkspace'
import { prefetchWorkspaces } from './services/prefetch'
const ToolLimitModal = lazy(() => import('./components/modals/settings/ToolLimitModal'))
const ArtifactsModal = lazy(() => import('./components/modals/files/ArtifactsModal'))
const SearchModal = lazy(() => import('./components/modals/chat/SearchModal'))
const SuggestionsModal = lazy(() => import('./components/modals/settings/SuggestionsModal'))
const ReflectionLessonsModal = lazy(() => import('./components/modals/settings/ReflectionLessonsModal'))
import HudView from './components/HudView'
import './App.css'
import { useFirstRunSetup } from './hooks/useFirstRunSetup'

// Declare pywebview API types
declare global {
  interface Window {
    pywebview?: {
      api?: Record<string, (...args: unknown[]) => Promise<unknown>>;
    };
    handleStateUpdate?: (state: string) => void;
    handleStatusText?: (text: string) => void;
    handleLiveStatus?: (text: string) => void;
    handleWaitNarrator?: (lines: string[]) => void;
    handleCaptionUpdate?: (text: string, append?: boolean) => void;
    handleAudioLevel?: (level: number) => void;
    handleVoiceState?: (payload: { phase: VoicePhase; transcript?: string | null; reply?: string | null; error?: string | null }) => void;
    handleVoiceAvailability?: (payload: VoiceAvailability) => void;
    handleDictationResult?: (payload: {
      success?: boolean;
      text?: string;
      message?: string;
      session?: number;
      reason?: string;
      status?: string;
    }) => void;
    handleFastTrackCommands?: (commands: FastTrackCommand[]) => void;
    handlePlannerEnabled?: (enabled: boolean) => void;
    handleTurnModeResolved?: (payload: { ui_mode?: 'auto' | 'agent' | 'chat' | 'plan'; mode?: string; source?: string }) => void;
    handleThinkingEvent?: (event: ThinkingEvent) => void;
    addAIMessageToChat?: (text: string, conversationData?: Conversation | string | null, messageId?: string, metadata?: Record<string, unknown> | null) => void;
    updateConversationInStore?: (conversation: Conversation) => void;
    addUserMessageToChat?: (text: string, conversationId?: string) => void;
    promptToolLimitExtension?: () => void;
    addFilePlanToChat?: (planMarkdown: string, approvalToken: string) => void;
    addToolApprovalToChat?: (payload: {
      token: string;
      conversationId?: string | null;
      tool_name?: string;
      decision_type?: 'accept_decline' | 'choice' | 'questions';
      prompt: string;
      title?: string;
      questions?: {
        question: string;
        header?: string;
        options: { label: string; description?: string }[];
        multiSelect?: boolean;
      }[];
      options?: { id: string; label: string }[];
    }) => void;
    handleTokenUsage?: (usage: TokenUsage) => void;
    handleMCPInstallRequest?: (payload: { server_id: string; title: string; reason?: string }) => void;
    /** Typed-stream sink driven by the Python emitter (gui_limb push_stream_event). */
    applyStreamEvent?: (events: StreamEvent[]) => void;
    /** Drop the in-flight typed turn (user hit Stop). */
    abortActiveTurn?: () => void;
    handleCapabilitySuggestion?: (payload: CapabilitySuggestion) => void;
    handleReflectionLesson?: (payload: ReflectionLesson) => void;
    require?: (module: string) => unknown;
  }
}

interface ContentBlock {
  type: string;
  text?: string;
  [key: string]: unknown;
}


interface BackendMessage {
  id: string;
  role: string;
  content: unknown;
  created_at: string;
  metadata?: Partial<ChatMessage> & {
    turn_duration_ms?: number;
    thinking_duration_ms?: number;
    user_rating?: number;
    clarifying_reply?: ChatMessage['clarifyingReply'];
  };
}

function App() {
  const urlParams = new URLSearchParams(window.location.search);
  const isHudMode = urlParams.get('mode') === 'hud';

  // Auth gate: browser clients must authenticate before connecting.
  // Desktop (pywebview) skips this entirely — it uses CHAD_SERVER_TOKEN directly.
  //
  // Same-machine browser access (localhost/127.0.0.1) skips it too: the Brain's
  // loopback trust (src/remote/auth.py::is_loopback_host, "token"/"hybrid" modes)
  // already accepts same-machine callers without checking the token, so forcing
  // a Google/email sign-in there verifies nothing — identity only matters once
  // traffic leaves the machine (Tailscale/remote PWA). A placeholder token is
  // seeded purely so brainSocket's "don't open without a token" guard proceeds;
  // the Brain ignores its value for loopback callers.
  //
  // Two browser paths for non-local access:
  //  - Supabase configured: gate on active Supabase session (Google OAuth / email).
  //  - Supabase not configured (self-hosted default): gate on a stored server token;
  //    the LoginScreen token form populates this and sets tokenConnected.
  const { session, loading: authLoading } = useAuth();
  const supabaseEnabled = isSupabaseConfigured();
  const localOrigin = isLocalOrigin();
  const [tokenConnected, setTokenConnected] = useState(() => hasCredentials());
  const needsLogin = isBrowserTransport() && !authLoading && !localOrigin && (
      supabaseEnabled ? !session : !tokenConnected
  );

  useEffect(() => {
    if (!isBrowserTransport() || !localOrigin || hasCredentials()) return;
    setToken('local-loopback');
    setTokenConnected(true);
  }, [localOrigin]);

  // The Brain rejected our stored token — clear it and drop back to the login screen so
  // the user can type in a new one, instead of the socket silently spamming reconnects
  // with the same bad credential forever (issue #451). Not applicable to loopback access,
  // which the Brain never rejects on token grounds.
  useEffect(() => {
    if (!isBrowserTransport() || supabaseEnabled || localOrigin) return;
    return brainSocket.onStatus((s) => {
      if (s === 'unauthorized') {
        setToken('');
        setTokenConnected(false);
      }
    });
  }, [supabaseEnabled, localOrigin]);

  const apiReady = useApiReady();
  const statusText = useStatusText();
    const {
    setApiReady, setServerUrl, setStatus, setStatusText,
    addChatMessage, addChatMessageWithActions, clearThinkingEvents, resetLiveTurnState,
    setUiMode, setConversations, setActiveConversation,
    setChatMessages, setHistoryLoading, setChatLoading, setChatSyncing, updateConversation, upsertConversation,
    setActiveModel, setAvailableCloudModels, setStreamingResponse,
    addConversations, setTokenUsage, setStreamConversationId, beginTurnWait
  } = useAppStore();
  const upsertCapabilitySuggestion = useAppStore(state => state.upsertCapabilitySuggestion);
  const upsertReflectionLesson = useAppStore(state => state.upsertReflectionLesson);
  const setReflectionLessons = useAppStore(state => state.setReflectionLessons);

  const status = useAppStore(state => state.status);
  const chatMessages = useAppStore(state => state.chatMessages);
  const uiMode = useAppStore(state => state.uiMode);
  const activeConversationId = useAppStore(state => state.activeConversationId);
  const isChatLoading = useAppStore(state => state.isChatLoading);
  const pendingDomainId = useAppStore(state => state.pendingDomainId);
  const setPendingDomain = useAppStore(state => state.setPendingDomain);
  const hasInitializedModelsRef = useRef(false);

  // First-run setup wizard (desktop only, issue #428): a fresh CHAD_HOME with
  // no usable LLM key shows this instead of the normal chat shell.
  const [needsSetup, setNeedsSetup, startupError, retryStartup] = useFirstRunSetup(apiReady, isBrowserTransport());

  // UI State Transition Logic
  useEffect(() => {
    // 1. Splash -> Chat Only (on first message)
    if (uiMode === 'splash' && chatMessages.length > 0) {
      setUiMode('chat_only');
    }
  }, [chatMessages, uiMode, setUiMode]);

  // Pending reflection lessons — Dashboard inbox (auto-applied rows live in Memory).
  const hasSyncedLessons = useRef(false);
  useEffect(() => {
    if (!apiReady) {
      hasSyncedLessons.current = false;
      return;
    }
    if (hasSyncedLessons.current) return;
    const api = window.pywebview?.api;
    if (!api || typeof api.get_reflection_lessons !== 'function') return;
    api.get_reflection_lessons(20).then((result) => {
      if (Array.isArray(result)) {
        setReflectionLessons(result as ReflectionLesson[]);
        hasSyncedLessons.current = true;
      }
    }).catch(console.error);
  }, [apiReady, setReflectionLessons]);

  // Modal states
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsInitialServerId, setSettingsInitialServerId] = useState<string | undefined>();
  const [settingsInitialCategory, setSettingsInitialCategory] = useState<'memory' | 'skills' | 'capabilities' | undefined>();
  const [toolLimitOpen, setToolLimitOpen] = useState(false);
  const [artifactsOpen, setArtifactsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [reflectionLessonsOpen, setReflectionLessonsOpen] = useState(false);

  // Workspace shell + command palette (icon-rail navigation).
  const activeWorkspace = useAppStore(state => state.activeWorkspace);
  const setActiveWorkspace = useAppStore(state => state.setActiveWorkspace);
  const commandPaletteOpen = useAppStore(state => state.commandPaletteOpen);
  const setCommandPaletteOpen = useAppStore(state => state.setCommandPaletteOpen);
  const isMobileNavOpen = useAppStore(state => state.isMobileNavOpen);
  const setIsMobileNavOpen = useAppStore(state => state.setIsMobileNavOpen);

  const openArtifact = useAppStore(state => state.openArtifact);
  const artifactViewerUseModal = useAppStore(state => state.artifactViewerUseModal);
  const closeArtifactViewer = useAppStore(state => state.closeArtifactViewer);


  // Install the window.* bridge handlers once (status, telemetry, finalize, and
  // the typed-stream sink window.applyStreamEvent). Live turn content renders from
  // the typed reducer; see ./runtime/streamRuntime and docs/features/STREAMING_REFACTOR.md.
  useEffect(() => {
    const uninstall = installStreamHandlers();
    return () => { uninstall(); };
  }, []);

  // Apply the user's stored accent color at boot (personalization, see utils/accent).
  useEffect(() => { initAccent(); }, []);

  // Global command-palette shortcut: Ctrl/Cmd+K toggles it from anywhere.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setCommandPaletteOpen(!useAppStore.getState().commandPaletteOpen);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [setCommandPaletteOpen]);

  useEffect(() => {
    window.updateConversationInStore = (conversation: Conversation) => {
      if (conversation && conversation.id) {
        upsertConversation(conversation);
      }
    };

    window.promptToolLimitExtension = () => {
      setToolLimitOpen(true);
    };

    window.handleMCPInstallRequest = (payload: { server_id: string; title: string; reason?: string }) => {
      setSettingsInitialServerId(payload.server_id);
      setSettingsOpen(true);
    };

    window.handleCapabilitySuggestion = (payload: CapabilitySuggestion) => {
      // New suggestion raised live by Chad's self-improvement loop — add to the
      // side panel queue (never the chat stream).
      if (payload && payload.id) {
        upsertCapabilitySuggestion(payload);
      }
    };

    window.handleReflectionLesson = (payload: ReflectionLesson) => {
      // Pending lessons only — auto-applied rows live in Settings → Memory.
      if (payload?.id && payload.status === 'pending') {
        upsertReflectionLesson(payload);
      }
    };

    window.addFilePlanToChat = (planMarkdown: string, approvalToken: string) => {
      // Thoughts are sourced from the typed streamTurn inside addChatMessageWithActions.
      addChatMessageWithActions('ai', planMarkdown, {
        type: 'file_plan_approval',
        approvalToken: approvalToken,
      });
    };

    window.addToolApprovalToChat = (payload) => {
      const state = useAppStore.getState();
      // Buffer until the turn bubble finalizes (approval is sent before ai_response).
      // Use streamTurn presence (not lifecycle) so we still attach if run_finished
      // landed before addAIMessageToChat in the same tick.
      if (state.streamTurn || state.activeTurn) {
        state.setPendingToolApproval(payload);
      } else {
        state.attachToolApprovalToLastAIMessage(payload);
      }
    };


    // Adaptive polling: Poll fast during discovery, slow down once connected
    const checkApi = () => {
      const hasApi = !!window.pywebview?.api;
      if (hasApi && !apiReady) {
        setApiReady(true);
        // console.log('[React] API detected - transitioning to slow heartbeat polling (3s)');
      } else if (!hasApi && apiReady) {
        setApiReady(false);
      }

      if (hasApi) {
        try {
          const api = window.pywebview!.api!;

          // 1. Sync Status Text (Fallback)
          if (typeof api.get_status === 'function') {
            api.get_status().then((result) => {
              if (typeof result === 'string' && result !== statusText) setStatusText(result);
            }).catch(console.error);
          }

          // 2. Sync State Enum (idle-only reconciliation).
          // The 3s poll used to overwrite status unconditionally, racing the
          // event-driven handleStateUpdate and clobbering a just-set mid-turn state
          // (a root cause of the "inconsistent processing/reasoning/outputting"
          // symptom). Now it only reconciles when NO turn is active, so events own
          // status during a turn and the poll merely recovers it when idle.
          if (typeof api.get_raw_state === 'function') {
            api.get_raw_state().then((result) => {
              if (typeof result === 'string') {
                const s = useAppStore.getState();
                // Only a running streamTurn blocks recovery — finished/orphaned slots
                // must not freeze status away from Brain's idle/ready.
                if (isLiveTurnBlockingStatusPoll(s)) return;
                const newState = result.toLowerCase() as AppStatus;
                if (newState !== s.status) setStatus(newState);
              }
            }).catch(console.error);
          }

          // 3. Sync Server URL (Rarely changes)
          if (typeof api.get_server_url === 'function') {
            api.get_server_url().then((url) => {
              if (typeof url === 'string') setServerUrl(url);
            }).catch(console.error);
          }

          // 4. Sync Active Model (Rarely changes)
          if (typeof api.get_active_model_info === 'function') {
            api.get_active_model_info().then((modelInfo) => {
              if (modelInfo) setActiveModel(modelInfo as ActiveModelInfo);
            }).catch(console.error);
          }
        } catch (e) {
          console.warn('[React] Could not call API method:', e);
        }
      }
    };

    const pollInterval = apiReady ? 3000 : 100; // 3s heartbeat once connected, 100ms discovery
    const interval = setInterval(checkApi, pollInterval);
    checkApi();

    return () => clearInterval(interval);
  }, [apiReady, status, statusText, setApiReady, setStatus, setStatusText, setToolLimitOpen, setActiveConversation, updateConversation, upsertConversation, setActiveModel, setServerUrl, setTokenUsage, addChatMessageWithActions, setStreamingResponse, upsertCapabilitySuggestion, upsertReflectionLesson]);

  // Persistent History Boot Sync
  const hasSyncedHistory = useRef(false);
  const messageQueueRef = useRef<{ text: string, contentBlocks: unknown[] | null, conversationId: string | null, category?: string, thinkingEnabled?: boolean | null, messageId: string }[]>([]);
  // Optimistic message snapshot for a chat with a turn still streaming, stashed when
  // the user navigates away. On return we restore this instead of the (mid-turn stale)
  // REST history so the triggering user prompt and prior bubbles aren't lost — the
  // in-flight turn's user message isn't persisted server-side until the turn completes.
  const bgMessagesRef = useRef<Record<string, ChatMessage[]>>({});
  // In-memory conversation cache for instant (SWR) thread switching
  const conversationCacheRef = useRef<Map<string, ChatMessage[]>>(new Map());
  // Tracks active in-flight history fetch to avoid race condition clobbers
  const activeFetchTargetRef = useRef<string | null>(null);

  // Keep in-memory cache synchronized with active conversation changes
  useEffect(() => {
    if (activeConversationId && chatMessages.length > 0) {
      conversationCacheRef.current.set(activeConversationId, chatMessages);
    }
  }, [activeConversationId, chatMessages]);

  const setMessageSendStatus = useAppStore(state => state.setMessageSendStatus);
  const failOutboundSend = useAppStore(state => state.failOutboundSend);
  useEffect(() => {
    // If API becomes not ready (disconnected), reset the sync flag so we sync again when it is ready
    if (!apiReady) {
      hasSyncedHistory.current = false;
      return;
    }

    // If status is not ready (e.g. thinking, listening, speaking), skip syncing for now but DO NOT reset the flag
    if (status !== 'ready') {
      return;
    }

    // Only attempt sync if we haven't synced yet
    if (hasSyncedHistory.current) {
      return;
    }

    const syncHistory = async (offset = 0) => {
      try {
        const api = window.pywebview?.api;
        if (api && typeof api.get_conversations === 'function') {
          // console.log(`[React] Starting history sync (offset: ${offset})...`);
          setHistoryLoading(true);
          const convs = await api.get_conversations(INITIAL_CONVERSATIONS_PAGE_SIZE, offset) as Conversation[];
          // console.log(`[React] History sync complete. Found ${convs.length} chats.`);
          
          if (offset === 0) {
            setConversations(convs, INITIAL_CONVERSATIONS_PAGE_SIZE);
          } else {
            addConversations(convs, INITIAL_CONVERSATIONS_PAGE_SIZE);
          }
          
          hasSyncedHistory.current = true;
          setHistoryLoading(false);
        } else {
          console.warn('[React] api.get_conversations not found even though apiReady is true');
        }
      } catch (e) {
        console.error('[React] Failed to sync history:', e);
        setHistoryLoading(false);
        // Schedule a retry in 3 seconds if status is still 'ready' and we haven't synced successfully yet
        setTimeout(() => {
          const currentStatus = useAppStore.getState().status;
          if (currentStatus === 'ready' && !hasSyncedHistory.current) {
            console.log('[React] Retrying history sync...');
            syncHistory(offset);
          }
        }, 3000);
      }
    };

    syncHistory();
  }, [apiReady, status, setConversations, addConversations, setHistoryLoading]);

  // Process queued messages once the API is ready and initializing is done
  useEffect(() => {
    if (apiReady && status !== 'initializing' && messageQueueRef.current.length > 0) {
      const queue = [...messageQueueRef.current];
      messageQueueRef.current = []; // Clear queue immediately to avoid double execution

      const processQueue = async () => {
        for (const msg of queue) {
          setStatus('thinking');
          setStatusText('Processing Prompt...');
          setStreamConversationId(msg.conversationId);
          beginTurnWait();
          setMessageSendStatus(msg.messageId, 'sending');

          try {
            const api = window.pywebview?.api;
            if (!api || typeof api.send_text_command !== 'function') {
              failOutboundSend(msg.messageId);
              continue;
            }
            await api.send_text_command(msg.text, null, msg.contentBlocks, msg.conversationId, null, msg.category, msg.thinkingEnabled ?? null);
            setMessageSendStatus(msg.messageId, null);
          } catch (err) {
            console.error('[React] Failed to send queued message:', err);
            failOutboundSend(msg.messageId);
          }
        }
      };

      processQueue();
    }
  }, [apiReady, status, setStatus, setStatusText, setStreamConversationId, beginTurnWait, setMessageSendStatus, failOutboundSend]);

  const handleLoadMoreConversations = async () => {
    const offset = useAppStore.getState().conversationsOffset;
    try {
      const api = window.pywebview?.api;
      if (api && typeof api.get_conversations === 'function') {
        setHistoryLoading(true);
        const convs = await api.get_conversations(CONVERSATIONS_PAGE_SIZE, offset) as Conversation[];
        addConversations(convs, CONVERSATIONS_PAGE_SIZE);
        setHistoryLoading(false);
      }
    } catch (e) {
      console.error('[React] Failed to load more history:', e);
      setHistoryLoading(false);
    }
  };

  // Model Discovery on Boot
  useEffect(() => {
    if (!apiReady || status === 'initializing' || hasInitializedModelsRef.current) return;
    hasInitializedModelsRef.current = true;

    const initModels = async () => {
      try {
        const api = window.pywebview?.api;
        if (!api) {
          hasInitializedModelsRef.current = false;
          return;
        }

        // console.log('[React] Initializing model data...');

        // 1. Get active model
        if (typeof api.get_active_model_info === 'function') {
          const info = await api.get_active_model_info();
          if (info) setActiveModel(info as ActiveModelInfo);
        }

        // 2. Get cloud models
        if (typeof api.get_available_models === 'function') {
          const models = await api.get_available_models();
          if (Array.isArray(models)) setAvailableCloudModels(models as { id: string; name: string }[]);
        }
        
        // DEFERRED: Local models scan has been moved to lazy on-click trigger in ModelSelector component!

      } catch (err) {
        console.error('[React] Model initialization failed:', err);
        hasInitializedModelsRef.current = false;
      }
    };

    initModels();
  }, [apiReady, status, setActiveModel, setAvailableCloudModels]);

  // Prefetch workspace data on boot so tabs load instantly
  const hasPrefetchedRef = useRef(false);
  useEffect(() => {
    if (!apiReady || status === 'initializing' || hasPrefetchedRef.current) return;
    hasPrefetchedRef.current = true;
    void prefetchWorkspaces();
  }, [apiReady, status]);


  // `force` re-loads a chat that's already active — used by the post-reconnect resync,
  // where the id hasn't changed but the on-screen messages may be arbitrarily stale.
  const handleChatSelect = async (id: string, opts?: { force?: boolean }) => {
    if (id === activeConversationId && !opts?.force) return;

    // A pending "new chat in <category>" only applies to a chat that hasn't been created
    // yet. Switching to an existing conversation abandons that intent — without this the
    // stale category would keep riding along on every send in the unrelated chat (harmless,
    // since the Brain re-resolves domain from the conversation row once one exists, but
    // still stale local state worth not carrying around).
    setPendingDomain(null);

    // Stash outgoing conversation in local cache and bgMessages if in-flight
    {
      const st = useAppStore.getState();
      if (st.activeConversationId && st.chatMessages.length > 0) {
        conversationCacheRef.current.set(st.activeConversationId, st.chatMessages);
      }
      if (st.streamTurn?.lifecycle === 'running'
          && st.activeConversationId
          && st.activeConversationId === st.streamConversationId) {
        bgMessagesRef.current[st.activeConversationId] = st.chatMessages;
      }
    }

    activeFetchTargetRef.current = id;

    const st = useAppStore.getState();
    const stashed = bgMessagesRef.current[id];
    const returningToRunningTurn =
      !!stashed && st.streamConversationId === id && st.streamTurn?.lifecycle === 'running';

    const cachedMessages = returningToRunningTurn ? stashed : conversationCacheRef.current.get(id);

    // Warm vs Cold transition:
    if (cachedMessages && !opts?.force) {
      // Warm switch: Render cached state instantly, with silent background SWR revalidation
      setChatMessages(cachedMessages);
      setActiveConversation(id);
      setChatLoading(false);
      setChatSyncing(true);
    } else {
      // Cold switch: Show skeleton screen
      setChatLoading(true);
      setActiveConversation(id);
      setChatSyncing(false);
    }

    const liveTurnRunning = st.streamTurn?.lifecycle === 'running';
    if (!liveTurnRunning) {
      resetLiveTurnState();
      clearThinkingEvents();
    }
    setTokenUsage(null);

    // Reset UI mode if in splash
    if (uiMode === 'splash') setUiMode('chat_only');
    setIsMobileNavOpen(false);

    try {
      if (window.pywebview?.api && typeof window.pywebview.api.get_chat_messages === 'function') {
        const messages = await window.pywebview.api.get_chat_messages(id) as BackendMessage[];
        
        // Race condition guard: discard if user moved to another chat while fetching
        if (activeFetchTargetRef.current !== id) {
          return;
        }

        // Map backend messages to frontend format
        const mappedMsgs = messages.map(m => {
          const rawThoughts = m.metadata?.thoughts || [];
          const normalizedThoughts: ThinkingEvent[] = rawThoughts.map((rawEvt: unknown) => {
            const t = normalizeTurnEvent(rawEvt);
            // Memory protection: Truncate large results in history
            if (t.result && typeof t.result === 'string' && t.result.length > 5000) {
              return { ...t, result: t.result.substring(0, 100) + "... (truncated history)" };
            }
            return t;
          });

          return {
            id: m.id,
            role: (m.role === 'assistant' ? 'ai' : m.role) as 'user' | 'ai',
            text: extractTextFromContent(m.content),
            timestamp: new Date(m.created_at),
            in_progress: m.metadata?.in_progress === true,
            turnDurationMs: m.metadata?.turn_duration_ms,
            thinkingDurationMs: m.metadata?.thinking_duration_ms ?? m.metadata?.thinkingDurationMs,
            userFeedback: (m.metadata?.user_rating === 1 ? 'positive' : m.metadata?.user_rating === -1 ? 'negative' : undefined) as 'positive' | 'negative' | undefined,
            prompt_tokens: m.metadata?.prompt_tokens,
            completion_tokens: m.metadata?.completion_tokens,
            total_tokens: m.metadata?.total_tokens,
            reasoning_tokens: m.metadata?.reasoning_tokens,
            context_window: m.metadata?.context_window,
            ttft: m.metadata?.ttft,
            stop_reason: m.metadata?.stop_reason,
            speed: m.metadata?.speed,
            actions: m.metadata?.actions,
            clarifyingReply: m.metadata?.clarifying_reply ?? m.metadata?.clarifyingReply,
            attachments: m.metadata?.attachments,
            artifacts: m.metadata?.artifacts || (normalizedThoughts.length > 0 ? normalizedThoughts.filter(e => e.type === 'tool_end' && e.success && e.download_token) : undefined),
            // Sources must be derived BEFORE the truncation below — it would otherwise
            // corrupt the JSON-stringified web_search payload in `result` before it can
            // be parsed (see src/clients/web/src/utils/sources.ts).
            sources: (() => {
              if (m.metadata?.sources && Array.isArray(m.metadata.sources)) return m.metadata.sources;
              const s = extractSourcesFromThoughts(m.metadata?.thoughts);
              return s.length > 0 ? s : undefined;
            })(),
            thoughts: normalizedThoughts.length > 0 ? normalizedThoughts : undefined,
          };
        });

        conversationCacheRef.current.set(id, mappedMsgs);

        // Prefer stashed optimistic turn if streaming is active
        const currentSt = useAppStore.getState();
        const activeReturningToRunning =
          !!stashed && currentSt.streamConversationId === id && currentSt.streamTurn?.lifecycle === 'running';

        setChatMessages(activeReturningToRunning ? stashed : mappedMsgs);
        delete bgMessagesRef.current[id];
      }
    } catch (err) {
      console.error('Failed to load chat history:', err);
    } finally {
      if (activeFetchTargetRef.current === id) {
        setChatLoading(false);
        setChatSyncing(false);
      }
    }
  };

  // Re-pull the open conversation after the socket comes back from a real interruption
  // (phone backgrounded, network dropped). Re-authenticating alone left whatever was in
  // memory on screen, so a turn that finished while the PWA was suspended never appeared —
  // the "it doesn't keep up" symptom. The server is the source of truth here by definition:
  // the socket that was streaming into this view is gone, so any local optimistic state is
  // strictly less trustworthy than a fresh fetch.
  //
  // Held in a ref because `handleChatSelect` is re-created every render — subscribing to it
  // directly would tear down and re-add the listener on each one.
  const resyncRef = useRef<() => void>(() => {});
  resyncRef.current = () => {
    const activeId = useAppStore.getState().activeConversationId;
    if (!activeId) return;
    void handleChatSelect(activeId, { force: true });
  };
  useEffect(() => {
    if (!isBrowserTransport()) return;
    return brainSocket.onResync(() => resyncRef.current());
  }, []);

  // Single entry point for workspace navigation — closes the Settings page so it
  // never lingers over a different workspace (fixes open-while-open layering).
  const navigateWorkspace = (workspace: Parameters<typeof setActiveWorkspace>[0]) => {
    setSettingsOpen(false);
    setActiveWorkspace(workspace);
    setIsMobileNavOpen(false);
  };

  const handleNewChat = (domainId?: string | null) => {
    // Stash outgoing conversation in local cache
    const st = useAppStore.getState();
    if (st.activeConversationId && st.chatMessages.length > 0) {
      conversationCacheRef.current.set(st.activeConversationId, st.chatMessages);
    }
    activeFetchTargetRef.current = null;
    setChatLoading(false);
    setChatSyncing(false);

    // Session isolation (issue #269): a fresh chat must start with zero carried-over
    // turn state — including any buffered approval / clarifying-question card. But a
    // turn still streaming in another chat is backgrounded, not aborted — preserve it
    // so it finishes and is there when the user returns to that chat.
    const liveTurnRunning = useAppStore.getState().streamTurn?.lifecycle === 'running';
    if (!liveTurnRunning) {
      resetLiveTurnState();
      clearThinkingEvents();
    }
    setTokenUsage(null);
    setActiveConversation(null);
    setChatMessages([]);
    setUiMode('splash');
    setIsMobileNavOpen(false);
    // The conversation row is created lazily on first send, so the chosen category is
    // parked here until then. Always set it (including to null) so starting a plain new
    // chat clears a category left over from the previous one.
    setPendingDomain(domainId ?? null);
  };

  const handleSendMessage = async (text: string, contentBlocks?: unknown[], attachments?: AttachedFile[], category?: string, thinkingEnabled?: boolean) => {
    // Fast-track: "/<alias>" skips the LLM/planner entirely and calls the tool
    // directly. Only aliases the Brain allowlisted (tools.fast_track_commands) match.
    const trimmed = text.trim();
    if (trimmed.toLowerCase() === '/skills' && !attachments?.length) {
      setSettingsInitialCategory('skills');
      setSettingsOpen(true);
      return;
    }
    if (trimmed.startsWith('/')) {
      const alias = trimmed.slice(1).split(/\s+/)[0];
      const fastTrackCommands = useAppStore.getState().fastTrackCommands;
      const skills = readCache<{ name: string }[]>('skills_catalog') || [];
      const isSkill = skills.some(s => s.name.replaceAll('_', '-').toLowerCase() === alias.replaceAll('_', '-').toLowerCase());
      const command = !isSkill && trimmed === `/${alias}` && !attachments?.length
        ? fastTrackCommands.find(c => c.alias === alias) : undefined;
      if (command) {
        const fastId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
        addChatMessage('user', trimmed, undefined, undefined, fastId, { sendStatus: 'sending' });
        useAppStore.getState().setTokenUsage(null);
        try {
          const api = window.pywebview?.api;
          if (!api || typeof api.invoke_fast_track !== 'function') {
            failOutboundSend(fastId);
            return;
          }
          await api.invoke_fast_track(alias);
          setMessageSendStatus(fastId, null);
        } catch (e) {
          console.error('[React] Fast-track invoke failed:', e);
          failOutboundSend(fastId);
        }
        return;
      }
    }

    const blocks = contentBlocks as ContentBlock[];
    // Allow message if we have text OR content blocks (multimodal)
    const hasContent = text.trim() || (blocks && blocks.length > 0);
    if (!hasContent) return;

    // Add user message to chat UI
    let displayText = text.trim();
    if (!displayText && blocks) {
      const hasImage = blocks.some((b: ContentBlock) => b.type === 'image');
      const hasDoc = blocks.some((b: ContentBlock) => b.type === 'document');
      const hasTextAttach = blocks.some((b: ContentBlock) => b.type === 'text' && typeof b.text === 'string' && b.text.includes('<file title='));
      
      if (hasImage && hasDoc) displayText = '[Image & Document]';
      else if (hasImage) displayText = '[Image]';
      else if (hasDoc) displayText = '[Document]';
      else if (hasTextAttach) displayText = '[Attached File]';
    }

    // Map raw attachments to store format (Exclude 'content' to prevent prompt explosion in history)
    const mappedAttachments: Attachment[] = attachments?.map(a => ({
      id: a.id,
      name: a.name,
      type: (a.type === 'binary' ? 'document' : a.type) as 'text' | 'image' | 'document',
      size: a.size,
      mediaType: a.mediaType || ''
      // content: a.content  <-- Dropped purposely
    })) || [];

    const outboundId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    addChatMessage('user', displayText, mappedAttachments, undefined, outboundId, { sendStatus: 'sending' });

    const isSystemReady = apiReady && status !== 'initializing';
    if (!isSystemReady) {
      messageQueueRef.current.push({
        text: text.trim(),
        contentBlocks: contentBlocks || null,
        conversationId: activeConversationId,
        category: category,
        thinkingEnabled: thinkingEnabled ?? null,
        messageId: outboundId,
      });
      return;
    }

    let targetConversationId = activeConversationId;
    // Captured before row creation clears it, so the send below can still carry it for the
    // browser-client fallback path (Brain creates the row itself when none was pre-allocated).
    const turnDomain = pendingDomainId;

    // Pre-allocate Supabase conversation row to give LangGraph a fresh, isolated session UUID
    if (!targetConversationId) {
      try {
        const api = window.pywebview?.api;
        if (api && typeof api.create_new_chat === 'function') {
          console.log('[React] Pre-allocating fresh chat row before starting execution turn...');
          // The domain is stamped on the row at creation. From here on the Brain reads it
          // back off the row, so this is the only moment the client decides the scope.
          const newChat = await api.create_new_chat(null, 'New Chat', turnDomain) as Conversation;
          if (newChat && newChat.id) {
            targetConversationId = newChat.id;
            upsertConversation(newChat);
            setActiveConversation(targetConversationId);
            setPendingDomain(null);
          }
        }
      } catch (e) {
        console.error('[React] Failed to pre-allocate chat ID:', e);
      }
    }
    
    // Update active conversation timestamp locally to resort sidebar instantly
    if (targetConversationId) {
      updateConversation(targetConversationId, { updated_at: new Date().toISOString() });
    }

    // model_loading is only set when the Brain publishes ModelLoading — not on every
    // local send. Optimistic model_loading stuck for the whole turn when Gemma was
    // already resident (status flip missed / browser StatusUpdate path never cleared it).
    setStatus('thinking');
    setStatusText('Processing Prompt...');
    setStreamConversationId(targetConversationId);
    beginTurnWait();

    try {
      const api = window.pywebview?.api;
      if (!api || typeof api.send_text_command !== 'function') {
        failOutboundSend(outboundId);
        return;
      }
      const meta = mappedAttachments.length > 0 ? { attachments: mappedAttachments } : null;
      await api.send_text_command(text.trim(), null, contentBlocks || null, targetConversationId, meta, category, thinkingEnabled ?? null, turnDomain);
      setMessageSendStatus(outboundId, null);
    } catch (e) {
      console.error('[React] Error sending message:', e);
      failOutboundSend(outboundId);
    }
  };

  const handleRetryFailedSend = async (messageId: string, text: string) => {
    const failedMsg = useAppStore.getState().chatMessages.find(m => m.id === messageId);
    const targetConversationId = failedMsg?.conversationId || activeConversationId;
    const contentBlocks = failedMsg?.contentBlocks || null;
    const category = failedMsg?.category || null;
    const thinkingEnabled = failedMsg?.thinkingEnabled ?? null;
    const domain = failedMsg?.domain || pendingDomainId || null;

    setMessageSendStatus(messageId, 'sending');
    setStatus('thinking');
    setStatusText('Processing Prompt...');
    setStreamConversationId(targetConversationId);
    beginTurnWait();
    try {
      const api = window.pywebview?.api;
      if (!api || typeof api.send_text_command !== 'function') {
        failOutboundSend(messageId);
        return;
      }
      const retryMeta = failedMsg?.attachments && failedMsg.attachments.length > 0 ? { attachments: failedMsg.attachments } : null;
      await api.send_text_command(
        (text || failedMsg?.text || '').trim(),
        null,
        contentBlocks,
        targetConversationId,
        retryMeta,
        category,
        thinkingEnabled,
        domain
      );
      setMessageSendStatus(messageId, null);
    } catch (e) {
      console.error('[React] Retry send failed:', e);
      failOutboundSend(messageId);
    }
  };

  const handleRetryPrompt = async (messageId: string, text: string) => {
    const isRunning = status === 'thinking' || useAppStore.getState().streamTurn?.lifecycle === 'running';
    if (isRunning) return;

    const messages = useAppStore.getState().chatMessages;
    const targetIdx = messages.findIndex(m => m.id === messageId);
    if (targetIdx === -1) return;
    const targetMsg = messages[targetIdx];

    const trimmedText = (text || targetMsg.text || '').trim();
    if (!trimmedText) return;

    // Prune history up to this prompt (standard Claude & ChatGPT retry & branch behavior)
    // Update targetMsg's text in case it was edited in the inline editor
    const prunedMessages = messages.slice(0, targetIdx + 1).map((m, idx) =>
      idx === targetIdx ? { ...m, text: trimmedText, sendStatus: 'sending' as const } : m
    );
    setChatMessages(prunedMessages);

    // Reset live turn state so fresh turn stream mounts cleanly without ghost artifacts
    resetLiveTurnState();
    clearThinkingEvents();
    setStreamingResponse(null);

    const targetConversationId = activeConversationId;
    const domain = targetMsg?.domain || pendingDomainId || null;
    const category = targetMsg?.category || undefined;
    const thinkingEnabled = targetMsg?.thinkingEnabled ?? null;

    setStatus('thinking');
    setStatusText('Processing Prompt...');
    setStreamConversationId(targetConversationId);
    beginTurnWait();

    try {
      const api = window.pywebview?.api;
      if (!api || typeof api.send_text_command !== 'function') {
        failOutboundSend(messageId);
        return;
      }
      await api.send_text_command(
        trimmedText,
        null,
        null,
        targetConversationId,
        null,
        category,
        thinkingEnabled,
        domain,
        true,
        targetIdx
      );
      setMessageSendStatus(messageId, null);
    } catch (e) {
      console.error('[React] Retry prompt failed:', e);
      failOutboundSend(messageId);
    }
  };

  const handleDispatchKanbanCard = (card: KanbanCard) => {
    // Card id is included so Chad can call kanban_board update_status directly
    // (no fuzzy title matching) as it moves the card through the board.
    const taskLine = `Please complete this task from my kanban board: "${card.title}" (kanban card_id: ${card.id})`;
    const prompt = card.description ? `${taskLine}\n\n${card.description}` : taskLine;
    // Persist the handoff onto the board itself (todo -> in_progress) so the
    // card stays visible and reflects that Chad picked it up, instead of
    // just firing a chat message with no trace on the board.
    if (card.column_id === 'todo') {
      window.pywebview?.api?.move_kanban_card?.(card.id, 'in_progress', 0);
    }
    setActiveWorkspace('chat');
    handleSendMessage(prompt);
  };

  const handleToolLimitConfirm = () => {
    setToolLimitOpen(false);
    if (window.pywebview?.api && typeof window.pywebview.api.confirm_tool_limit_extension === 'function') {
      window.pywebview.api.confirm_tool_limit_extension(true);
    }
  };

  const handleToolLimitCancel = () => {
    setToolLimitOpen(false);
    if (window.pywebview?.api && typeof window.pywebview.api.confirm_tool_limit_extension === 'function') {
      window.pywebview.api.confirm_tool_limit_extension(false);
    }
  };


  // Sidebar content

  // Controls Content (Floating Dock)
  const controlsContent = (
    <ControlDock
      onSettingsClick={() => setSettingsOpen(true)}
      onDashboardClick={() => setActiveWorkspace('dashboard')}
      onAutomationsClick={() => setActiveWorkspace('automations')}
      onKanbanClick={() => setActiveWorkspace('board')}
      onRoutinesClick={() => setActiveWorkspace('routines')}
      onSuggestionsClick={() => setSuggestionsOpen(true)}
      onReflectionLessonsClick={() => setReflectionLessonsOpen(true)}
    />
  );

  if (isHudMode) {
    return <HudView />;
  }

  // Show login screen while auth is resolving or when no credentials in browser mode.
  // Local origin never waits on Supabase's session fetch — it isn't gating on it.
  if ((!localOrigin && authLoading) || needsLogin) {
    return (
      <Suspense fallback={null}>
        <LoginScreen onConnect={() => setTokenConnected(true)} />
      </Suspense>
    );
  }

  if (!isBrowserTransport() && needsSetup === null) {
    return <StartupScreen failed={startupError} onRetry={retryStartup} />;
  }

  // First-run setup wizard: select a model before showing Chat.
  if (needsSetup) {
    return (
      <Suspense fallback={<StartupScreen />}>
        <SetupWizard onComplete={() => setNeedsSetup(false)} />
      </Suspense>
    );
  }

  return (
    <>
      <BrowserConnectModal />
      <ConnectionBanner />
      <div className={`chad-root ${isMobileNavOpen ? 'mobile-nav-is-open' : ''}`}>
        {/* Backdrop (and selecting a nav item) closes the drawer — no X over the rail. */}
        {!isMobileNavOpen && (
          <button
            type="button"
            className="chad-mobile-nav-toggle"
            onClick={() => setIsMobileNavOpen(true)}
            aria-label="Open navigation"
            aria-expanded={false}
          >
            <Menu size={20} />
          </button>
        )}

        <div
          className="chad-mobile-backdrop"
          onClick={() => setIsMobileNavOpen(false)}
          aria-hidden="true"
        />

        <div className="chad-rail-wrapper">
          <WorkspaceRail
            onSelectWorkspace={navigateWorkspace}
            settingsActive={settingsOpen}
            onCommandPalette={() => setCommandPaletteOpen(true)}
            onSettingsClick={() => setSettingsOpen(true)}
          />
        </div>

        <div className="chad-stage">
          <div
            className={`chad-stage-panel ${activeWorkspace === 'chat' ? 'is-active' : 'is-hidden'}`}
            style={{ display: activeWorkspace === 'chat' ? 'contents' : 'none' }}
          >
            <AppShell
              nav={<NavSidebar
                onNewChat={handleNewChat}
                onChatSelect={handleChatSelect}
                onLoadMore={handleLoadMoreConversations}
                onArtifactsClick={() => setArtifactsOpen(true)}
                onSearchClick={() => setSearchOpen(true)}
              />}
              sidebar={
                <ChatPanel
                  orb={<ChadAvatarLive size={128} />}
                  chat={isChatLoading ? <ChatSkeleton /> : <ChatStream onRetryFailedSend={handleRetryFailedSend} onRetryPrompt={handleRetryPrompt} />}
                  input={
                    <div className="input-wrapper">
                      <ChatInput
                        onSend={handleSendMessage}
                        disabled={status === 'error'}
                        placeholder={status === 'error' ? (statusText.toLowerCase().includes("lm studio") ? "LM Studio Offline — Please start LM Studio and enable the local server..." : "Offline — Please check if your server is up and running...") : (!apiReady || status === 'initializing') ? "Ask Chad... (connecting to server)" : "Ask Chad..."}
                      />
                      {uiMode === 'splash' && status !== 'initializing' && status !== 'error' && (
                        <SuggestedActions onSelect={(text, category) => handleSendMessage(text, undefined, undefined, category)} />
                      )}
                    </div>
                  }
                  controls={controlsContent}
                  status={statusText}
                />
              }
              uiMode={uiMode}
              artifactPanel={
                openArtifact && !artifactViewerUseModal ? (
                  <ArtifactSidePanel
                    artifact={openArtifact}
                    onClose={closeArtifactViewer}
                  />
                ) : undefined
              }
            />
          </div>

          <div
            className={`chad-stage-panel ${activeWorkspace === 'routines' ? 'is-active' : 'is-hidden'}`}
            style={{ display: activeWorkspace === 'routines' ? 'flex' : 'none', width: '100%', height: '100%' }}
          >
            <RoutinesWorkspace />
          </div>

          <div
            className={`chad-stage-panel ${activeWorkspace === 'board' ? 'is-active' : 'is-hidden'}`}
            style={{ display: activeWorkspace === 'board' ? 'flex' : 'none', width: '100%', height: '100%' }}
          >
            <BoardWorkspace onDispatchToChad={handleDispatchKanbanCard} />
          </div>

          <div
            className={`chad-stage-panel ${activeWorkspace === 'dashboard' ? 'is-active' : 'is-hidden'}`}
            style={{ display: activeWorkspace === 'dashboard' ? 'flex' : 'none', width: '100%', height: '100%' }}
          >
            <DashboardWorkspace />
          </div>

          <div
            className={`chad-stage-panel ${activeWorkspace === 'automations' ? 'is-active' : 'is-hidden'}`}
            style={{ display: activeWorkspace === 'automations' ? 'flex' : 'none', width: '100%', height: '100%' }}
          >
            <AutomationsWorkspace />
          </div>

          {/* Settings is a full-stage page that covers the active workspace. */}
          <AnimatePresence>
            {settingsOpen && (
              <SettingsView
                onClose={() => { setSettingsOpen(false); setSettingsInitialServerId(undefined); setSettingsInitialCategory(undefined); }}
                initialServerId={settingsInitialServerId}
                initialCategory={settingsInitialCategory}
                onUseSkill={(name) => {
                  setSettingsOpen(false);
                  setActiveWorkspace('chat');
                  window.dispatchEvent(new CustomEvent('chad:compose-skill', { detail: name }));
                }}
              />
            )}
          </AnimatePresence>
        </div>
      </div>

      <AnimatePresence>
        {commandPaletteOpen && (
          <CommandPalette
            onClose={() => setCommandPaletteOpen(false)}
            onNavigate={navigateWorkspace}
            onNewChat={() => { navigateWorkspace('chat'); handleNewChat(); }}
            onOpenSettings={() => setSettingsOpen(true)}
            onOpenMemory={() => { setSettingsInitialCategory('memory'); setSettingsOpen(true); }}
            onOpenSkills={() => { setSettingsInitialCategory('skills'); setSettingsOpen(true); }}
            onOpenSearch={() => setSearchOpen(true)}
          />
        )}
      </AnimatePresence>

      {openArtifact && artifactViewerUseModal && (
        <ArtifactViewerModal
          isOpen
          onClose={closeArtifactViewer}
          filename={openArtifact.filename}
          token={openArtifact.token}
          serverUrl={openArtifact.serverUrl}
        />
      )}

      {/* Modals */}
      <Suspense fallback={null}>
        <ArtifactsModal isOpen={artifactsOpen} onClose={() => setArtifactsOpen(false)} />
        <SearchModal
          isOpen={searchOpen}
          onClose={() => setSearchOpen(false)}
          onSelectChat={handleChatSelect}
        />
        <SuggestionsModal isOpen={suggestionsOpen} onClose={() => setSuggestionsOpen(false)} />
        <ReflectionLessonsModal isOpen={reflectionLessonsOpen} onClose={() => setReflectionLessonsOpen(false)} />
        <ToolLimitModal
          isOpen={toolLimitOpen}
          onConfirm={handleToolLimitConfirm}
          onCancel={handleToolLimitCancel}
        />
      </Suspense>

      <NotificationOverlay />
      <VoiceOverlay onOpenSettings={() => setSettingsOpen(true)} />
      <PushBanner />
    </>
  );
}

export default App
