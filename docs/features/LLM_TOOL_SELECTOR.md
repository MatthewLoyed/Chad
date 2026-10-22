# LLM-Based Tool Selector

## The Problem

Before this feature, every turn fed Chad's main model a bloated tool set regardless of what the user actually asked. The registry injected **10 mandatory core tools** on every single turn, then the Supabase RAG pipeline added up to 8 more based on semantic similarity. A query as simple as "check my email and turn on the lamp" would result in **17 tools** being passed to the main model — most of which were irrelevant noise that increased latency, cost, and the chance of wrong tool selection.

```
"check my email and turn on the lamp"  →  17 tools:
  ask_user_question, search_for_tool, list_skills, load_skill,
  call_mcp_tool, read_file, write_file, run_host_command, list_directory,
  search_gmail, gmail_check_inbox, get_gmail_history,
  hubspace_control, system_settings, control_volume, manage_heartbeat,
  capture_screen
```

## The Solution

A small local model (e.g. Qwen 3.5 9B) sees the **entire tool catalog** and returns only the names of the tools actually needed. The main model (35B) only ever sees those tools.

The selector is **backend-agnostic**: it talks OpenAI-compatible `/chat/completions` and routes
every model-residency decision (which model is loaded, load/unload, resident-model listing)
through the shared provider abstraction (`providers` + `model_lifecycle`), so it works against
LM Studio, Ollama, or a llama.cpp/vLLM static endpoint with no backend-specific code of its own.
Provider quirks (auth-key placeholder, `/v1/models` vs `/api/ps` resident-listing) come from
`provider_capabilities` and each backend's `ProviderLifecycle`, not from URL sniffing.

Load/unload of the selector model is delegated to the shared coordinator in
[`MODEL_LIFECYCLE.md`](MODEL_LIFECYCLE.md) — this doc covers selection logic only.

```
"check my email and turn on the lamp"  →  2 tools:
  search_gmail, hubspace_control
```

Average reduction across all query types: **~90% fewer tools fed to the main model.**

## How It Works

```
User query
    │
    ▼
LLMToolSelector (Qwen 3.5 9B, ~5s)
    │  Receives: full tool catalog (name + description for each tool)
    │  Returns: ["search_gmail", "hubspace_control"]
    │
    ▼
ToolRegistry.get_schemas_for_turn()
    │  Merges: 9B selection + 3 permanent core tools
    │  Applies: exclusion rules (MCP, skills, specialist categories)
    │
    ▼
Main model (35B) receives tool schemas for exactly those tools
```

The 9B model receives a structured prompt with the full catalog as a JSON dictionary and is instructed to return a raw JSON array of tool name strings — no explanation, no markdown, just names. The registry validates every name against `self._tools` before resolving to a schema, so hallucinated names are silently dropped.

## Permanent Core Tools

Three tools are always injected regardless of what 9B selects:

| Tool | Why it's permanent |
|---|---|
| `ask_user_question` | Structured clarifying questions when the request is ambiguous |
| `search_for_tool` | Needed when 9B's selection misses a capability |
| `list_skills` | Meta-discovery: the model can't include skills it doesn't know exist |

Everything else — including tools that were previously forced-core like `read_file`, `write_file`, `run_host_command`, `list_directory` — is now selected on demand by 9B.

## Graceful Fallback

The LLM selector is opportunistic. On first use it pings the configured inference backend and caches whether the selector model is available. If the backend isn't running, the model isn't loaded, or any request fails, `get_schemas_for_turn` falls back silently to the existing Supabase RAG pipeline. No configuration change is needed to switch between modes — it happens automatically.

```
Backend reachable?
    ├── Yes → LLMToolSelector.select() → exact tool set
    └── No  → SemanticToolSelector.retrieve_relevant_tools() → RAG results (existing behavior)
```

## Configuration

```yaml
# config/config.yaml
tools:
  llm_selector_model: "qwen/qwen3.5-9b"  # Model ID as shown in LM Studio
```

The base URL is read from the configured provider under `local_models.providers.*.base_url`
(LM Studio defaults to `http://localhost:1234/v1`, Ollama to `:11434`). The base_url alone
resolves the backend identity via `providers.resolve_provider_name`, so pointing the selector
at Ollama or a static endpoint requires no code change.

To swap to a different small model, update `tools.llm_selector_model`. The model needs to:
- Be accessible via the backend's OpenAI-compatible API
- Return clean JSON arrays reliably at `temperature: 0`

## Benchmarks

Tested against 8 representative queries using the full 74-tool catalog:

| Query | Before | After | Reduction |
|---|---|---|---|
| check my email and turn on the lamp | 17 | 2 | 88% |
| open spotify and set volume to 50% | 17 | 2 | 88% |
| what time is it | 16 | 1 | 93% |
| remind me in an hour to call mom | 17 | 1 | 94% |
| take a screenshot | 17 | 1 | 94% |
| find my resume and zip it | 15 | 2 | 86% |
| what homework do i have due | 16 | 1 | 93% |
| mute youtube, dim lights, remind me to sleep | 17 | 3 | 82% |

**Average: 16.5 → 1.6 tools. Zero hallucinations across all tests.**

Latency for the 9B selection call: ~4–7s. This runs before the main model turn, but can be overlapped with other pre-turn work (system prompt construction, history loading) in future optimizations.

## Files

| File | Role |
|---|---|
| `src/infrastructure/tools/selection/llm_selector.py` | `LLMToolSelector` — catalog builder, HTTP caller, JSON parser, name validator |
| `src/infrastructure/tools/registry.py` | Integration point — `get_schemas_for_turn()` tries LLM selector, falls back to RAG |
| `config/config.yaml` | `tools.llm_selector_model` config key |
| `tests/manual/test_9b_tool_selector.py` | Initial v1 proof-of-concept (30 tools, 10 queries) |
| `tests/manual/test_9b_tool_selector_v2.py` | v2 comparison: full registry vs RAG candidates vs strict prompt |
| `tests/manual/test_9b_full_replace.py` | Before/after tool count benchmark (the definitive test) |
