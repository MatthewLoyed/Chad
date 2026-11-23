# Hermes Agent Architecture — Deep Analysis & Integration Plan for Chad

**Date**: May 13, 2026
**Branch**: `research/hermes-agent-integration`
**Hermes Version**: 0.13.0 (~15,775 lines in `run_agent.py` alone, 3,935 objects in repo)

---

## Part 1: Where Hermes Excels (and Why)

### 1. Closed Learning Loop — The Killer Feature

**What it does**: Hermes has a self-improving agent that creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions.

**How it works**:
- **Curator** (`agent/curator.py`, 1,782 lines): Background orchestrator that runs inactivity-triggered (no cron daemon). When the agent is idle, it spawns a forked AIAgent to review agent-created skills — pinning, archiving, consolidating, or patching them based on derived activity timestamps. Uses lifecycle states with auto-transitions, respects pinned skills, never auto-deletes (only archives).
- **Skill Manager Tool** (`tools/skill_manager_tool.py`): Allows the agent to autonomously create, edit, patch, and delete skills — turning successful approaches into reusable procedural knowledge.
- **Session Search** (`tools/session_search_tool.py`, 613 lines): FTS5 full-text search over SQLite session transcripts, ranked by relevance, grouped by session, summarized by an auxiliary LLM. Returns focused summaries of past conversations rather than raw transcripts.
- **Honcho Integration**: Dialectic user modeling — the agent builds a model of who you are across sessions.

**Why it's good**: This transforms the agent from a stateless tool into a system that gets better over time. Every complex task the agent completes can become a skill. Every conversation becomes searchable. The curator ensures skills don't rot. Chad historically lacked agent-writable skills and a curator; **session/transcript search has since landed** (GUI `SearchModal` + hybrid `search_session_history`: chat-history FTS first, vector `session_log` fallback — see `MEMORY_AND_CONTEXT.md`).

**Chad gap (updated)**: Skill CRUD + curator still open. The TODOS.md “find that cool yt vid” recall path is covered by transcript FTS, not by Hermes-style session_search alone.
---

### 2. Subagent Delegation with True Isolation

**What it does**: Spawns child AIAgent instances with isolated context, restricted toolsets, and their own terminal sessions. Supports single-task and batch (parallel) modes. Parent blocks until all children complete.

**How it works** (`tools/delegate_tool.py`, 2,768 lines):
- Each child gets a fresh conversation (no parent history), its own `task_id` (own terminal session, file ops cache), a restricted toolset, and a focused system prompt
- Parent's context only sees the delegation call and summary result, never child's intermediate tool calls or reasoning
- Tool blocklist prevents recursive delegation, user interaction, shared memory writes, cross-platform side effects, and code execution in children
- Depth cap (default 1, max 3) prevents infinite nesting
- Thread-safe approval callbacks routed via `threading.local()` to avoid deadlocking the parent TUI
- Pause flag + active subagent registry for observability
- Configurable max concurrent children (default 3)

**Why it's good**: Chad's supervisor pattern uses `langgraph-supervisor` with 8 specialist agents, but they share context and the supervisor sees everything. Hermes' delegation creates truly isolated subprocesses — the parent only gets a summary. This means:
- **No context pollution**: Child's tool calls don't bloat parent's context window
- **Parallel execution**: Multiple independent workstreams can run simultaneously
- **Security**: Blocked tools prevent children from escaping their sandbox
- **Observability**: Parent can pause, monitor, or interrupt subagents

**Chad gap**: Chad's specialists share a graph state. Hermes' subagents are truly isolated processes. Chad's `output_mode="last_message"` optimization is clever but doesn't achieve true isolation.

---

### 3. Mixture of Agents (MoA) for Complex Reasoning

**What it does**: Leverages multiple frontier LLMs in parallel for extremely difficult problems, then synthesizes results with an aggregator model.

**How it works** (`tools/mixture_of_agents_tool.py`, 543 lines):
- Reference models (Claude Opus, Gemini Pro, GPT-5, DeepSeek) generate diverse initial responses in parallel
- Aggregator model (Claude Opus) synthesizes into a single high-quality output
- Based on peer-reviewed research (arXiv:2406.04692v1)
- Configurable reference models, temperatures, and minimum successful references
- Retry logic per model with graceful failure handling

**Why it's good**: For the hardest tasks, no single model is optimal. MoA combines diverse reasoning paths. Chad has `ModelRouter` for failover but NOT for ensemble reasoning.

**Chad gap**: Chad's `ModelRouter` does provider failover, not ensemble reasoning.

---

### 4. Kanban Multi-Agent Task Coordination

**What it does**: Structured task board for orchestrating multiple worker agents with lifecycle management.

**How it works** (`tools/kanban_tools.py`, 1,140 lines):
- Dispatcher spawns isolated workers, each scoped to a single task
- Workers report via lifecycle tools (complete, block, heartbeat, comment)
- Orchestrator profiles route work via kanban board
- Tools are gated — only visible to dispatcher-spawned workers or orchestrator profiles
- Backend portable — works regardless of terminal backend (Docker, Modal, SSH, etc.)
- SQLite-backed task tracking

**Why it's good**: This is a structured, database-backed approach to multi-agent coordination. Chad's supervisor is graph-based (LangGraph) which is elegant but doesn't persist task state across sessions. Kanban survives restarts.

**Chad gap**: Chad's LangGraph checkpointing persists conversation state but not structured task assignments.

---

### 5. Tool Call Guardrails (Loop Detection)

**What it does**: Per-turn tool-call loop detection with configurable warnings and hard stops.

**How it works** (`agent/tool_guardrails.py`, 459 lines):
- Tracks exact failure repeats, same-tool failure repeats, and no-progress patterns
- Classifies tools as idempotent vs. mutating
- Warnings are enabled by default; hard stops are opt-in
- Configurable thresholds per pattern type
- Side-effect free — returns decisions, runtime owns enforcement

**Why it's good**: Chad has anti-loop protections (max 3-5 tool calls, hard iteration ceiling of 8, recursion limit of 15) but they're blunt limits. Hermes detects actual loop patterns — repeated failures, same tool failing repeatedly, no-progress spirals. This is much more precise.

**Chad gap**: Chad's anti-loop is a sledgehammer (hard limits). Hermes' is a scalpel (pattern detection).

---

### 6. Pluggable Context Compression Engine

**What it does**: Abstract base class for context management with built-in compressor implementation.

**How it works**:
- `agent/context_engine.py` (207 lines): ABC defining the compression interface
- `agent/context_compressor.py` (1,556 lines): Built-in implementation with:
  - Structured summary template with Resolved/Pending question tracking
  - "Remaining Work" replaces "Next Steps" to avoid reading as active instructions
  - Iterative summary updates (preserves info across multiple compactions)
  - Token-budget tail protection instead of fixed message count
  - Tool output pruning before LLM summarization (cheap pre-pass)
  - Scaled summary budget proportional to compressed content
  - Richer tool call/result detail in summarizer input
  - Protection of head (first N) and tail (last N) messages
  - Auxiliary model for cheap summarization

**Why it's good**: This is a first-class, pluggable system. Third-party engines can replace the built-in compressor. The summary template is carefully designed to prevent the LLM from re-answering already-resolved questions.

**Chad gap**: Chad's context caching is DISABLED due to Gemini's 32k minimum. No compression engine exists. Chad relies on LangGraph checkpointing for state but has no context compaction strategy for long conversations.

---

### 7. Memory Architecture with Frozen Snapshot Pattern

**What it does**: Bounded, file-backed memory that persists across sessions with a frozen snapshot for the system prompt.

**How it works** (`tools/memory_tool.py`, 587 lines):
- Two stores: MEMORY.md (agent's personal notes) and USER.md (what the agent knows about the user)
- **Frozen snapshot pattern**: System prompt is stable at session start; mid-session writes update disk immediately but do NOT change the system prompt. This preserves the prefix cache for the entire session.
- Character limits (not tokens) because char counts are model-independent
- File locking for read-modify-write safety (fcntl on Unix, msvcrt on Windows)
- Content scanning for prompt injection/exfiltration patterns
- Entry delimiter (§) for clean multiline entries

**Why it's good**: The frozen snapshot pattern is brilliant — it means the prefix cache stays hot for the entire session. Chad injects memory into the system prompt but doesn't have this stability guarantee.

**Chad gap**: Chad's MemoryEngine uses Supabase vector embeddings which is more powerful but also more complex and doesn't have the prefix-cache stability guarantee.

---

### 8. Structured Error Classification & Smart Failover

**What it does**: Centralized error taxonomy with priority-ordered classification pipeline.

**How it works** (`agent/error_classifier.py`, 1,059 lines):
- 20+ categorized error types (auth, billing, rate_limit, overloaded, context_overflow, model_not_found, provider_policy_blocked, etc.)
- Disambiguates between billing exhaustion and transient rate limits
- Recovery hints per error: `retryable`, `should_compress`, `should_rotate_credential`, `should_fallback`
- Provider-specific pattern matching for Anthropic, OpenAI, Gemini, etc.
- Used by main retry loop in `run_agent.py` for every API failure

**Why it's good**: Chad has `provider_profiles.yaml` for multi-provider failover but the error classification is implicit. Hermes explicitly classifies every error and maps it to a recovery strategy.

**Chad gap**: Chad's failover is provider-based rotation. Hermes' is error-type-based with specific recovery actions.

---

### 9. Lazy Dependency Installation

**What it does**: Installs provider-specific dependencies on first use, not at setup time.

**How it works** (`tools/lazy_deps.py`, 443 lines):
- Allowlist of lazy-installable backends
- Venv-scoped only (never touches system Python)
- PyPI by package name only (no `--index-url` overrides, no `git+https://`)
- Opt-out via `security.allow_lazy_installs: false`
- Offline detection with clear error messages
- Responded to the "Mini Shai-Hulud worm" supply chain attack — exact pinning + lazy install = smaller blast radius

**Why it's good**: Chad has `requirements.txt` and `requirements-voice.txt` but everything is installed eagerly. Hermes' approach means a supply-chain attack on one optional dependency doesn't break every install.

**Chad gap**: Not critical for Chad (which is a personal project, not a distributed package), but the philosophy of minimal core + lazy extras is worth adopting.

---

### 10. Prompt Injection Defense

**What it does**: Scans context files and memory content for injection patterns before injecting into system prompt.

**How it works**:
- `_CONTEXT_THREAT_PATTERNS` in `prompt_builder.py`: 13 regex patterns detecting "ignore previous instructions", "do not tell the user", "system prompt override", etc.
- Invisible unicode detection (zero-width spaces, BOM, directional overrides)
- Same scanning applied to memory content in `memory_tool.py`
- Blocked content is replaced with a descriptive warning, not silently dropped

**Why it's good**: This is a defense-in-depth approach. Chad has no equivalent scanning.

**Chad gap**: Chad's skill system and memory system have no injection scanning.

---

### 11. Messaging Gateway Architecture

**What it does**: Single gateway process supporting Telegram, Discord, Slack, WhatsApp, Signal, and Email.

**How it works** (`gateway/` — 23 files):
- Platform registry for extensible backend support
- Session management with pairing (DM pairing for security)
- Delivery routing, streaming consumer, hooks system
- Slash command support across all platforms
- Voice memo transcription
- Cross-platform conversation continuity

**Why it's good**: Chad has Telegram integration via `src/remote/` but Hermes' gateway is a unified, extensible system with session management, pairing, and hooks.

**Chad gap**: Chad's Telegram integration is separate from the core. Hermes unifies all messaging through one gateway.

---

### 12. Cron Scheduling with Platform Delivery

**What it does**: Built-in cron scheduler with delivery to any messaging platform.

**How it works** (`cron/` module):
- Cron expressions, intervals, one-shot jobs
- Executed in isolated sessions (no prior context)
- Gateway ticks scheduler every 60 seconds
- File lock prevents duplicate execution
- Natural language scheduling

**Why it's good**: Chad has `BackgroundAgentManager` but no structured cron system with cross-platform delivery.

---

### 13. Tool Registry with AST-Based Discovery

**What it does**: Self-registering tool modules discovered via AST analysis.

**How it works** (`tools/registry.py`, 564 lines):
- Each tool module calls `registry.register()` at module level
- Discovery via AST inspection (finds `registry.register()` calls without importing modules)
- Circular-import safe import chain
- TTL-cached `check_fn` results (30s) — external state changes on human timescales
- Dynamic schema overrides for runtime-dependent fields
- `ToolEntry` with metadata: name, toolset, schema, handler, check_fn, requires_env, is_async, description, emoji, max_result_size_chars

**Why it's good**: Chad's `ToolDiscoveryOrchestrator` scans filesystem but doesn't use AST for circular-import safety. Hermes' approach is more robust.

---

### 14. Exact Dependency Pinning (Supply Chain Security)

**What it does**: Every direct dependency is exact-pinned to `==X.Y.Z` (no ranges).

**Why**: Tightened on 2026-05-12 in response to the "Mini Shai-Hulud worm" hitting `mistralai` 2.4.6 on PyPI. If that release had been captured by `mistralai>=2.3.0,<3`, every install in the hours before quarantine would have pulled it.

**Chad gap**: Chad uses standard `requirements.txt` without exact pinning philosophy.

---

## Part 2: What Hermes Does NOT Do Better (Chad's Advantages)

| Area | Chad wins because... |
|------|---------------------|
| **Voice/VUI** | Chad has full voice pipeline (STT, TTS, wake word, audio routing). Hermes has voice memo transcription but not real-time voice conversation. |
| **GUI/HUD** | Chad has a React web UI + Electron HUD overlay. Hermes has a TUI (terminal UI) and web dashboard but no desktop overlay. |
| **LangGraph Integration** | Chad's hierarchical multi-agent graph with checkpointing is more structured than Hermes' flat agent loop. |
| **Supabase/Postgres** | Chad's cloud persistence with vector embeddings is more powerful than Hermes' SQLite + FTS5. |
| **DDD Architecture** | Chad's domain-driven design layers (Application, Domain, Infrastructure, Core, Clients) provide cleaner separation than Hermes' flat module structure. |
| **Local AI Sovereignty** | Chad's LM Studio + Ollama integration with dynamic model discovery is more local-first. |
| **E2B Sandbox** | Chad's secure code execution in isolated sandboxes is more mature. |
| **Configuration System** | Chad's multi-layer config merging (config.yaml -> secrets.yaml -> preferences.yaml -> env vars) is more flexible. |
| **EventBus** | Chad's decoupled pub/sub event system is cleaner than Hermes' callback-heavy approach. |

---

## Part 3: Problem Mapping — Does Hermes Solve Chad's Documented Issues?

| Chad Problem | Hermes Solution | Applicable? |
|--------------|-----------------|-------------|
| **TODOS.md: Search feature** ("find that cool yt vid") | Session Search (FTS5 + LLM summarization) | **YES — direct match** |
| **SessionManager at 1,199 lines** (exceeds 800-line target) | Hermes splits across focused modules (memory_manager, context_engine, error_classifier) | **YES — architectural pattern** |
| **Dead MCP code** | Hermes has MCP but it's properly lazy-loaded via `tools/lazy_deps.py` | **YES — lazy loading pattern** |
| **Legacy callback coexistence with EventBus** | Hermes uses callbacks but has a cleaner gateway hooks system | **PARTIAL** |
| **Context caching disabled** (Gemini 32k minimum) | Hermes' context compressor works with ANY context size — it compresses, not caches | **YES — alternative approach** |
| **Skill sync is manual** | Hermes' curator auto-maintains skills | **YES — automation** |
| **Model switching is global** | Hermes' auxiliary routing allows per-task model selection | **YES — per-task routing** |
| **Anti-loop is blunt** (hard limits) | Hermes' tool guardrails detect actual loop patterns | **YES — smarter detection** |
| **Memory reset granularity** | Hermes' frozen snapshot + character limits are more precise | **PARTIAL** |
| **Dual-path maintenance burden** | Hermes has CLI vs. Gateway but shares the same `run_agent.py` core | **YES — unified core** |
| **Streaming bubble issues** | Hermes' StreamingContextScrubber handles chunk boundaries | **YES — streaming robustness** |
| **Supply chain security** | Exact pinning + lazy deps | **PARTIAL — philosophy** |

---

## Part 4: Why Hermes is So Good (Design Principles)

1. **Single Core, Multiple Frontends**: One `run_agent.py` powers CLI, Gateway, Cron, Kanban workers, and batch runner. Chad has dual paths (Local Brain vs. Headless Brain) that require mirrored changes.

2. **Progressive Loading Everywhere**: Lazy deps, lazy skill loading, TTL-cached checks, frozen memory snapshots. Nothing is loaded until needed.

3. **Defense in Depth**: Prompt injection scanning, tool guardrails, exact dep pinning, venv-scoped installs, file locking, multimodal scrubbing, error classification.

4. **Self-Improvement as a First-Class Feature**: The curator + skill manager + session search form a closed loop. The agent gets better over time.

5. **Isolation by Default**: Subagents get fresh context, blocked tools, depth limits, and thread-safe approvals. No context pollution.

6. **Pluggable Architecture**: Context engines, providers, skill sources, gateway platforms — all defined by ABCs with runtime selection.

7. **Operational Maturity**: 13 release notes (v0.2.0 through v0.13.0), supply chain incident response, shutdown forensics, rate limiting, credential pools, budget tracking.

8. **Research-Backed**: MoA based on peer-reviewed paper, session search uses FTS5 (battle-tested SQLite extension), context compression uses auxiliary models (proven pattern).

---

## Part 5: Integration Plan for Chad

### Phase 1: High-Impact, Low-Risk (Quick Wins)

#### 1.1 Session Search (FTS5 + LLM Summarization)
**Solves**: TODOS.md search feature
**Effort**: Medium
**Status (2026-07)**: **Landed (partial).** Chat-history FTS5 + snippets on local sqlite, GUI `SearchModal`, and hybrid `search_session_history` (FTS first → vector `session_log` fill). Still out of scope vs original Hermes writeup: auxiliary LLM summarization of whole matching sessions, `/search` slash command.

**What was built**:
- SQLite FTS5 index over `messages` (+ conversation title) with sync triggers + backfill
- Search tool hybrid over full transcripts, with snippet truncation for the agent
- GUI SearchModal consumes `snippet` when present

**Original Hermes extras (not done)**:
- Auxiliary LLM summarization of top N matching sessions
- `/search` slash command for the HUD

#### 1.2 Tool Call Guardrails (Loop Detection)
**Solves**: Blunt anti-loop limits
**Effort**: Low
**What to build**:
- Port `ToolCallGuardrailConfig` and `ToolCallGuardrailController` to Chad
- Integrate into the supervisor's per-turn observation loop
- Classify existing Chad tools as idempotent vs. mutating
- Configurable thresholds in `config.yaml`

**Files to create/modify**:
- `src/core/orchestration/tool_guardrails.py` (new)
- Modify supervisor to consult guardrails before each tool dispatch
- Add `tool_loop_guardrails` section to `config/config.yaml`

#### 1.3 Prompt Injection Scanning
**Solves**: No defense against malicious skill/memory content
**Effort**: Low
**What to build**:
- Port `_CONTEXT_THREAT_PATTERNS` and `_MEMORY_THREAT_PATTERNS` to Chad
- Apply to skill loading, memory injection, and context file scanning
- Block and log rather than silently drop

**Files to create/modify**:
- `src/core/security/prompt_scanner.py` (new)
- Wire into `SkillDiscoveryOrchestrator`, `MemoryEngine`, and prompt assembly

### Phase 2: Architectural Improvements

#### 2.1 Context Compression Engine
**Solves**: Disabled context caching, no compression strategy
**Effort**: High
**What to build**:
- Port `ContextEngine` ABC and `ContextCompressor` implementation
- Integrate into both Brain paths (Local and Headless)
- Use auxiliary model for cheap summarization
- Structured summary template with Resolved/Pending/Active Task tracking
- Tool output pruning before LLM summarization

**Files to create/modify**:
- `src/core/orchestration/context_engine.py` (new, ABC)
- `src/core/orchestration/context_compressor.py` (new, implementation)
- Wire into `ConversationOrchestrator` and `TextModeClient` pre-turn pipeline
- Config in `config.yaml` for threshold percent, protect_first_n, protect_last_n

#### 2.2 Curator System (Skill Lifecycle Management)
**Solves**: Manual skill sync, static skills
**Effort**: High
**What to build**:
- Background curator that reviews agent-created skills on inactivity
- Lifecycle states: active -> stale -> archived (never auto-deleted)
- Pin mechanism to protect important skills
- Skill self-improvement during use (agent suggests patches based on execution results)
- Integration with existing Supabase skill index

**Files to create/modify**:
- `src/core/orchestration/curator.py` (new)
- `src/core/orchestration/skill_lifecycle.py` (new)
- Extend existing skill system with lifecycle metadata
- Add `.curator_state` to skill directories

#### 2.3 Subagent Delegation with True Isolation
**Solves**: Shared context between specialists, no parallel execution
**Effort**: Very High
**What to build**:
- Port `delegate_task` with isolated AIAgent instances
- Tool blocklist for children (no recursive delegation, no shared memory writes)
- Parallel batch mode for independent workstreams
- Depth control (configurable max nesting)
- Thread-safe approval callback routing
- Summary-only results back to parent

**Files to create/modify**:
- `src/core/tools/delegate_tool.py` (new)
- `src/core/orchestration/subagent_manager.py` (new)
- Modify supervisor to offer delegation as an option alongside specialist routing
- Config: `delegation.max_concurrent_children`, `delegation.max_spawn_depth`, `delegation.max_iterations`

#### 2.4 Structured Error Classification
**Solves**: Implicit failover, no error-type-specific recovery
**Effort**: Medium
**What to build**:
- Port `FailoverReason` enum and `ClassifiedError` dataclass
- Priority-ordered classification pipeline
- Map error types to recovery actions (retry, rotate, compress, fallback, abort)
- Provider-specific pattern matching

**Files to create/modify**:
- `src/core/intelligence/error_classifier.py` (new)
- Wire into `LiteLLMAdapter` and all LLM call paths
- Extend `ModelRouter` to use classified errors for failover decisions

### Phase 3: Advanced Features

#### 3.1 Mixture of Agents (MoA)
**Solves**: Single-model reasoning limit
**Effort**: Medium
**What to build**:
- Port MoA tool for complex reasoning tasks
- Parallel reference model queries with aggregator synthesis
- Configurable reference models and aggregator
- Opt-in via toolset (not default — expensive)

**Files to create/modify**:
- `src/core/tools/mixture_of_agents_tool.py` (new)
- Add to tool registry as opt-in toolset

#### 3.2 Frozen Memory Snapshot Pattern
**Solves**: Prefix cache instability
**Effort**: Medium
**What to build**:
- Adopt Hermes' frozen snapshot pattern for memory injection
- System prompt memory is stable at session start
- Mid-session memory writes update disk but not system prompt
- Snapshot refreshes on next session start

**Files to create/modify**:
- Modify `MemoryEngine` to maintain `_system_prompt_snapshot` separately from live state
- Apply to both Supabase and SQLite memory backends

#### 3.3 Lazy Dependency Loading
**Solves**: Eager loading of unused dependencies
**Effort**: Medium
**What to build**:
- Port lazy dependency system for optional tool backends
- Allowlist-based, venv-scoped installation
- Opt-out via config flag

**Files to create/modify**:
- `src/infrastructure/lazy_deps.py` (new)
- Apply to optional tool backends (browser, smart home, media, etc.)

#### 3.4 Kanban Task Coordination
**Solves**: No persistent task tracking across sessions
**Effort**: Very High
**What to build**:
- SQLite-backed kanban board for multi-agent task coordination
- Dispatcher to spawn isolated workers
- Lifecycle tools (complete, block, heartbeat, comment)
- Orchestrator profiles for routing work

**Files to create/modify**:
- `src/core/orchestration/kanban/` (new module)
- `src/core/tools/kanban_tools.py` (new)
- Integration with supervisor for complex, multi-step tasks

### Phase 4: Operational Hardening

#### 4.1 Exact Dependency Pinning
**Effort**: Low
**What to do**: Pin all direct dependencies in `requirements.txt` to exact versions with comments justifying each pin.

#### 4.2 Unified Core (Reduce Dual-Path Burden)
**Solves**: Dual-path maintenance burden
**Effort**: Very High (refactor)
**What to build**:
- Converge `ConversationOrchestrator` and `TextModeClient` into a single core agent loop
- Platform-specific delivery (GUI vs. WebSocket) becomes a thin adapter layer
- Shared core = shared bug fixes, shared features

#### 4.3 Streaming Context Scrubber
**Solves**: Streaming bubble issues across delivery paths
**Effort**: Low
**What to build**:
- Port `StreamingContextScrubber` to handle chunk boundaries in streaming responses
- Apply to both `EventBridge` (local) and `gui_limb.py` (remote)

---

## Part 6: What NOT to Port

| Hermes Feature | Why skip for Chad |
|----------------|------------------|
| **Messaging Gateway** | Chad has different priorities (desktop-first, voice-first). Telegram integration exists. |
| **Cron Scheduling** | Chad has `BackgroundAgentManager`. Cron is nice-to-have, not critical. |
| **Honcho User Modeling** | Requires external `honcho-ai` dependency. Chad's Supabase memory is sufficient. |
| **Seven Terminal Backends** | Chad is Windows-only. Docker/Modal/Singularity don't apply. |
| **RL Training / Atropos** | Research feature, not relevant for production assistant. |
| **Skills Hub (GitHub-based)** | Chad's skill system is local + Supabase. Hub adds attack surface. |
| **Computer Use (macOS)** | Chad is Windows-only. |
| **Matrix / DingTalk / Feishu** | Not relevant platforms for Chad's user base. |

---

## Part 7: Recommended Priority Order

Based on impact vs. effort and alignment with Chad's documented pain points:

1. **Session Search** — Directly solves TODOS.md feature request, medium effort, high user value
2. **Tool Guardrails** — Low effort, immediately improves agent reliability
3. **Prompt Injection Scanning** — Low effort, security improvement
4. **Context Compression Engine** — High effort but solves the disabled-caching problem
5. **Streaming Context Scrubber** — Low effort, fixes documented streaming bugs
6. **Structured Error Classification** — Medium effort, improves failover intelligence
7. **Frozen Memory Snapshot** — Medium effort, improves prefix cache stability
8. **Curator System** — High effort, enables self-improvement loop
9. **Subagent Delegation** — Very high effort, but transforms the agent architecture
10. **MoA** — Medium effort, opt-in enhancement for complex tasks
11. **Unified Core** — Very high effort refactor, but reduces long-term maintenance

---

## Appendix: Key Hermes Files Reference

| Hermes File | Lines | What it does |
|-------------|-------|--------------|
| `run_agent.py` | 15,775 | Main agent loop, tool calling, error recovery |
| `tools/delegate_tool.py` | 2,768 | Subagent delegation with isolation |
| `skills/skills_hub.py` | 3,262 | Skills hub with provenance tracking |
| `agent/curator.py` | 1,782 | Background skill maintenance |
| `agent/context_compressor.py` | 1,556 | Context window compression |
| `agent/error_classifier.py` | 1,059 | API error taxonomy & recovery |
| `tools/memory_tool.py` | 587 | File-backed memory with frozen snapshot |
| `tools/kanban_tools.py` | 1,140 | Multi-agent task coordination |
| `tools/session_search_tool.py` | 613 | FTS5 session search + LLM summarization |
| `agent/tool_guardrails.py` | 459 | Tool-call loop detection |
| `tools/lazy_deps.py` | 443 | Lazy dependency installation |
| `tools/mixture_of_agents_tool.py` | 543 | Multi-model ensemble reasoning |
| `tools/skill_manager_tool.py` | 932 | Agent-managed skill CRUD |
| `agent/prompt_builder.py` | 1,457 | System prompt assembly + injection scanning |
| `tools/registry.py` | 564 | Self-registering tool discovery |
| `agent/context_engine.py` | 207 | Pluggable context engine ABC |
