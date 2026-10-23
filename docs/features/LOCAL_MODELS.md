# 🤖 Local AI Models Integration

Chad supports a high-performance local AI orchestration layer that allows the assistant to run entirely offline or in a hybrid "Local Brain" configuration using **LM Studio**, **Ollama**, or any raw OpenAI-compatible endpoint (**llama.cpp `server`**, **vLLM**).

## Overview

### Local hardware inventory (issue #5)

`src/infrastructure/services/hardware_assessment.py` provides a read-only scan
of the process host: physical CPU cores, total/available RAM, individual GPUs,
dedicated/free VRAM where reliable, and available model storage. It does not
probe remote inference hosts or upload hardware telemetry. Missing tools,
drivers or capacity values remain unknown; no detected GPU permits CPU-only
assessment. Windows uses NVIDIA's query or the registry's QWORD memory value,
never the truncated WMI `AdapterRAM` DWORD. Shared GPU memory is not extra RAM.
`setup.hardware_probe_timeout_s` bounds each external probe. Callers should run
the scan outside the UI thread and permit rescan/manual correction.

The local model integration uses an OpenAI-compatible adapter to bridge between the `ConversationOrchestrator` and local inference servers. It supports:
- **Automatic Discovery**: Scans for running servers on local ports.
- **Capability Verification**: Sends a "probe" request to verify if the model supports tool-calling (ReAct).
- **Graceful Failover**: If a local model fails, it can fall back to cloud (if configured).
- **Provider-agnostic lifecycle**: `src/core/intelligence/providers/` resolves each backend to a `ProviderLifecycle` strategy (`LMStudioLifecycle`, `OllamaLifecycle`, or `StaticEndpointLifecycle` for llama.cpp/vLLM/anything else) so load/unload/VRAM-budget logic never branches on backend name (#450a).

### Recommended vs. supported

- **LM Studio** is the *recommended* backend — it's what Chad's day-to-day development and production instance run on, so it gets the most testing and the model-specific tuning in `config/model_registry.py` (thinking budgets, sampling defaults, the froggeric chat template for Qwen 3.6 35B).
- **Ollama** and **raw OpenAI-compatible endpoints** (llama.cpp `server`, vLLM) are *first-class supported* — the provider-capability plumbing (below) exists specifically so LM Studio-only bugfixes don't leak into their request path, and the setup wizard's backend chooser (`src/infrastructure/services/setup_wizard.py`, issue #499) walks you through configuring any of them. They haven't had the same volume of live multi-turn tool-calling mileage as LM Studio, though, so if you hit a rough edge, please file it — the gating design below is meant to make backend-specific fixes cheap to add without touching LM Studio's path.

## Provider capability flags (issue #498)

A handful of LM Studio-specific bugs (Qwen3's json_schema binding to the reasoning channel instead of content — lmstudio-bug-tracker #1773; the froggeric chat template's non-standard tool-call XML; the `lm-studio` placeholder API key) used to be gated by ad-hoc `provider == "LM Studio"` / URL-sniffing checks scattered through the adapters. These are now declarative flags in [`src/core/intelligence/provider_capabilities.py`](../../src/core/intelligence/provider_capabilities.py), keyed on the same resolved provider identity (`lmstudio` / `ollama` / `static`) the lifecycle layer uses:

| Flag | LM Studio | Ollama / llama.cpp / vLLM |
|---|---|---|
| `needs_qwen3_reasoning_workaround` | ✅ | ❌ |
| `needs_tool_role_transform` (froggeric `<tool_response>` tags) | ✅ | ❌ |
| `has_native_tool_parser` | ❌ (dirty-parser fallback expected) | ✅ |
| `reasoning_effort_via_extra_body` | ✅ | ❌ |
| `default_api_key` | `lm-studio` | `None` |

Every flag defaults off for any provider not explicitly listed, so a new backend added to `config/model_registry.py` never inherits an LM Studio workaround by accident. `tests/unit/test_provider_capabilities.py` proves each workaround fires for LM Studio and stays inert for Ollama/static endpoints.

## First-launch Chat setup

Fresh installs leave the main model unselected. The desktop shows a startup
screen while its local service and setup status become available, with retry
if startup cannot complete. Once setup is ready, choose LM Studio, Ollama, or
another compatible model server. Existing saved choices are preserved even
when their server is temporarily offline.

**Use a cloud API** is a separate first-run route for hosted OpenAI-compatible
Chat Completions endpoints, with OpenRouter/DeepSeek address presets and manual
model-ID entry when discovery is unavailable. It requires no local model app or
hardware scan. The screen explains account/API-key requirements, possible usage
charges and where messages go. Native Anthropic Messages, Gemini and Responses
API onboarding is not provided by this route; existing credential-based cloud
routing and its model picker are a separate legacy configuration path.

For LM Studio, install/open the app, download a chat model suitable for your
computer, and start its Local Server from Model Server (Developer in older
versions). Chad checks the
server address directly and lists the chat models it exposes. The CLI is not
a prerequisite for connecting; installation detection and server readiness
are separate checks. If the server cannot be reached, the wizard shows what
to check and allows retry, Back, or Finish later. Finish later does not mark
Chat as ready: reopening Chad returns to setup until a model is chosen.

Select or enter a model ID, then explicitly send a short greeting through the
Brain's inference adapter before saving it to open Chat. The test can load a
local model or incur hosted usage, so it never runs automatically. It uses a
small requested output budget and checks for a usable text reply. Changing the
endpoint, credential or model invalidates the result. This establishes basic
inference, not a complete orchestrated Agent turn. Agent/tool compatibility and
optional accounts are configured separately in Settings. Core Chat does not
require downloading the specialist model catalog used by the developer script
below. This flow is implemented in the setup wizard and setup service; a new
installer still needs clean Windows acceptance before release.

For explicit hosted endpoints, preserve the complete provider model ID (including
namespaces) and do not send LM Studio/Ollama engine parameters. Physical hosting
metadata is separate from the legacy `local_models` explicit-endpoint routing
flag; that flag alone does not imply messages stay on this computer. Cloud
endpoint selections display a cloud indicator and omit local engine settings.
Discovery does not verify tool calling. DeepSeek's documented thinking switch
is scoped to its official API host; its multi-turn thinking/tool reasoning replay
is still unverified. Do not certify a provider from a successful greeting.

The [setup journey acceptance checklist](https://github.com/MatthewLoyed/Chad/issues/5#setup-journey-contract--proposed-acceptance-2026-10-23)
tracks proposed task-based model guidance, installation from nothing, existing
and remote setups, offline/recovery paths, accessibility and first-reply
verification. It distinguishes current evidence from unimplemented stories;
the hardware scan and mocked UI checks do not establish complete onboarding.

## Quick automated setup (issue #406)

New desktop installs use four setup stages: Welcome, Model app, Choose a model,
and Start chatting. **Find a model for me** (issue #5) scans with sensible defaults
and presents one recommendation; comparing models and viewing suggested load
settings are separate disclosures. Model folder/context overrides, detected
hardware, manual corrections, and optional assets live under Advanced.
Downloads require confirmation. Existing models can skip the scan and connect
directly. Memory validation is read-only;
benchmarking is a separate confirmation because it may change residency.
The CLI below retains the legacy multi-model preset for advanced users; it is
not the hardware-aware onboarding default and can exceed smaller machines.

```powershell
# Report-only: what's downloaded, is the server up
python scripts/setup/setup_lmstudio.py --check

# Interactive: confirms which models to grab, then downloads + starts the server
python scripts/setup/setup_lmstudio.py

# Non-interactive (CI / scripting / a future GUI wizard step, see #218)
python scripts/setup/setup_lmstudio.py --yes --json
```

This wraps LM Studio's own `lms` CLI (bundled with LM Studio >= 0.3.x and put
on PATH by the installer — nothing extra to install). It never touches
anything beyond LM Studio's model catalog and local server: **embeddings
(`nomic-ai/nomic-embed-text-v2-moe`) are not fetched through LM Studio at
all** — they run locally via `sentence-transformers` (`requirements.txt`),
downloaded only after explicit setup confirmation (or an intentional
`embeddings.allow_download: true` override). Uncached embeddings leave semantic
search/memory unavailable while basic Chat remains usable. If `lms` isn't found on
PATH, the script explains how to get it and falls back to the manual steps
below.

### Required models (what the script downloads by default)

| Model | Role | Approx. VRAM | Config key |
|---|---|---|---|
| `google/gemma-4-26b-a4b-qat` | Main chat model + judge | ~16GB | `local_models.active_model`, `reflection.model` |
| `qwen/qwen3.5-9b` | Tool selector | ~6GB | `tools.llm_selector_model` |
| `google/gemma-3-1b` | Intent router | ~1GB | `intent_router.model` |

These three are the legacy CLI preset, not a prerequisite for basic Chat.
Desktop setup selects one chat model, shares it with the tool selector and
compatible specialists, and disables the separate intent router. Explicit
specialist overrides remain respected. Pass
`--include-optional` to also grab the specialist models below.

`setup.share_main_model` keeps the selector aligned with later main-model
picker changes and refreshes an already-created selector without a Brain
restart. Set it false before deliberately configuring a separate selector.

### Optional / advanced models

| Model | Role | Approx. VRAM | Notes |
|---|---|---|---|
| `qwen/qwen2.5-coder-14b` | `code_agent` specialist | ~10GB | Only needed if you use the coding subagent. |
| `qwen/qwen3-14b` | General 14B alternative | ~10GB | |
| `qwen/qwen3-vl-8b` | Browser vision-assist | ~6GB | `browser_agent` is gated off by default in this release, so this isn't needed out of the box. |
| `qwen/qwen3.6-35b-a3b` | Heavier main-model option | ~24GB | Needs the froggeric chat template fix below — not plug-and-play like the others. |

### 12GB VRAM systems

Chad's defaults are tuned for a 16GB card. The 26B main model won't fit
alongside a separate 9B selector at 12GB. The simplest fallback (until a
dedicated smaller-tier config lands) is to collapse both roles onto a
single resident `qwen/qwen3.5-9b`: set `local_models.active_model` to
`qwen/qwen3.5-9b` and point `tools.llm_selector_model` at the same model
instead of loading a second one. This gives up some main-chat quality
compared to the 26B path but fits comfortably with room left for
embeddings and KV cache. For new installs, use the desktop hardware assessment
rather than downloading the legacy preset.

### Optional embedding assets

The hardware panel enumerates the optional Nomic CPU embedding assets (about
1.8 GiB of weights/tokenizer files and a small cross-repository custom-code
dependency). Download progress is pollable; completed Hugging Face cache files
survive retry and offline use. Both the primary Nomic snapshot and its
`nomic-ai/nomic-bert-2048` code are cached. Automatic runtime downloads default
off via `embeddings.allow_download`; source and frozen clients use the same
explicit job. A remote Brain needs its own cache: a local desktop download does
not provision a different machine. Optional voice/image/specialist models are
not required to finish this setup.

## Supported Backends

### Hardware recommendation engine (issue #5)

`model_recommendations.py` ranks a deliberately small offline catalog in
`config/hardware_models.json`. Artifact filenames, immutable Hub revisions,
file sizes, architecture dimensions, model-card links and verification dates
are explicit. Refresh those fields together when adding or replacing a model;
`capability_rank` is a curated preference, not a benchmark score. Current
candidates cover Qwen3 4B/8B/14B, Qwen3.5 4B/9B Q4_K_M, and a larger
Qwen3.6 35B-A3B UD-Q4_K_M option. Other models can
still be selected through the existing picker.

Fit uses weights + an FP16 K/V bound + runtime buffers, reserves the larger
of 4 GiB/15% RAM for the OS, the larger of 1 GiB/15% dedicated VRAM for display
and runtime headroom, and 3 GiB RAM for CPU embeddings. Host RAM also reserves
the mapped model weights even for GPU placement. Available memory caps
the budget. Hybrid-attention models deliberately use a conservative full-layer
K/V bound pending native validation. MoE speed uses active parameters while
memory always includes all expert weights. Each GPU is assessed separately; partial
CPU offload remains an advanced manual choice rather than a fast-path promise.
The 40–50 generated-token/sec target is a floor around 40, not an upper cap.
Known desktop GPUs receive a broad bandwidth heuristic; CPU/offload, unknown
and mobile GPUs get unknown speed. This is not measured performance or tool
quality certification. Longer context, thinking and concurrency change latency.

`lmstudio_assessment.py` checks the installed CLI's advertised flags and exact
downloaded model key before `lms load --estimate-only`. It never loads or
unloads a model or changes saved defaults. An explicitly confirmed benchmark
uses LM Studio's native API and its generation/first-token/load timing, with
chat storage disabled. This can JIT-load a model or change residency; it does
not benchmark remote endpoints. Unsupported API versions remain unknown.

`setup_assets.py` runs one explicitly confirmed background setup operation at
a time. Downloads use catalog-pinned artifacts, size verification and resumable
`.part` files under the user-selected LM Studio model directory, with catalog
SHA-256 validation before finalization. No servers,
drivers or installers are started. Polling does not download anything. Failed
operations require an explicit retry. `setup.network_timeout_s`,
`setup.download_timeout_s`, `setup.model_assessment_timeout_s` and
`setup.model_benchmark_tokens` own the transfer/measurement budgets.

The approach was researched against Odysseus's Cookbook/HW Fit and its llmfit
attribution; this implementation contains no copied upstream code. See the
[Odysseus implementation](https://github.com/odysseus-dev/odysseus/tree/dev/services/hwfit)
and [upstream attribution](https://github.com/odysseus-dev/odysseus/blob/dev/ACKNOWLEDGMENTS.md).

### 1. LM Studio
- **Port**: `1234`
- **Protocol**: OpenAI-compatible API.
- **Best For**: Desktop users who want a GUI to download and switch models.
- **Notes**: Ensure "Local Server" is started in LM Studio (or run the automated script above).

#### LM Studio Settings (Qwen 3.6 35B)
- **Model**: `Qwen3-35B` (or equivalent GGUF)
- **Chat Template**: Custom Jinja — using the community fixed template by `froggeric` (v20)
  - Source: https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates
  - File saved locally: `config/chat_template_qwen36_35b.jinja`
  - Apply in LM Studio: Model Settings → Chat Template → Custom Jinja → paste file contents
  - Fixes agentic stalling, KV cache invalidation, empty think block poisoning, and C++ parser crashes present in the official Qwen template

#### Code Compatibility Notes (froggeric template)

The froggeric template uses a non-standard tool call format that differs from the official Qwen template. Two adjustments were made in `src/core/intelligence/adapters/openai_adapter.py` to stay compatible:

**1. Dirty Parser — Froggeric `<function=...>` format support**

The froggeric template instructs the model to emit tool calls like this:
```
<tool_call>
<function=my_tool>
<parameter=location>London</parameter>
</function>
</tool_call>
```
This is NOT JSON, so the existing `json.loads()` fallback parser silently failed on it. A new pattern (0a in `_extract_markdown_tool_calls`) was added before the JSON check to handle this format. This parser only runs if LM Studio fails to convert the raw model output into the OpenAI `tool_calls` response format — under normal operation the native path handles it transparently.

**2. Tool Result Format — `<tool_response>` tags for Qwen3/LM Studio**

When a tool result is fed back to the model, local models don't support the OpenAI `tool` role natively so results are re-mapped to `user` messages. For Qwen3 on LM Studio, the froggeric template trains the model to expect results wrapped in `<tool_response>` tags:
```
<tool_response>
{result JSON}
</tool_response>
```
Using the old generic `[TOOL RESULT: name]` prefix caused the model to not recognise result boundaries, leading to repeated tool calls and mid-loop stalls. The transformer in `openai_adapter.py` now uses `<tool_response>` when `_is_qwen3` and the resolved provider's `needs_tool_role_transform` capability flag is set (LM Studio only — see [Provider capability flags](#provider-capability-flags-issue-498) above) and falls back to the generic prefix for all other local models.

### 2. Ollama
- **Port**: `11434`
- **Protocol**: OpenAI-compatible endpoint (`/v1`).
- **Best For**: Linux/macOS users and headless servers.
- **Notes**: Supports a wide range of GGUF and custom models. Tool-capable models to try: `qwen2.5`, `qwen2.5-coder`, `llama3.1`.
- **Setup**: `ollama pull qwen2.5 && ollama serve` (serves on `:11434` by default), then either run the setup wizard's backend chooser or set `config/preferences.yaml`:
  ```yaml
  local_models:
    use_local: true
    providers:
      ollama:
        base_url: http://localhost:11434/v1
        api_key: ollama
        enabled: true
    active_model:
      name: qwen2.5
      provider: ollama
      base_url: http://localhost:11434/v1
      api_key: ollama
  ```
  `local_models.active_model` must be the dict form shown above (not a bare model-name string) to route to Ollama — a bare string always resolves against `local_models.providers.lmstudio` (`ConfigManager.get_local_model_config`, `src/shared/config_manager.py:356-369`). The setup wizard (`save_provider_choice` in `setup_wizard.py`) writes exactly this shape for you.
- **Jinja tool-role handling**: Ollama's chat templates, like LM Studio's, generally don't support the OpenAI `tool` role — the adapter's generic `tool` → `user` remap (unconditional, not provider-gated) handles this already. The froggeric-specific `<tool_response>` tag format is LM Studio/Qwen3-only (see capability flags above); Ollama gets the plain `[TOOL RESULT: name]` prefix.

### 3. llama.cpp `server` / vLLM (raw OpenAI-compatible endpoint)
- **Port**: whatever you started the server on (llama.cpp `server` commonly `:8080`, vLLM commonly `:8000`).
- **Protocol**: OpenAI-compatible `/v1/chat/completions` + `/v1/models`.
- **Best For**: headless deployments, custom quantizations, or any server you're already running that speaks the OpenAI schema.
- **Lifecycle**: resolves to `StaticEndpointLifecycle` (`src/core/intelligence/providers/static_endpoint.py`) — no load/unload API assumed, the model the server was started with is treated as always-resident.
- **Config-only setup** (no code changes — validates `StaticEndpointLifecycle` end-to-end):
  ```yaml
  local_models:
    use_local: true
    active_model:
      name: my-served-model-id      # whatever the server reports at GET /v1/models
      provider: static               # any key that isn't "lmstudio"/"ollama" also resolves to static
      base_url: http://localhost:8000/v1
      api_key: not-needed            # most llama.cpp/vLLM setups don't check this
  ```
  The setup wizard's "custom endpoint" option (`probe_local_backend(provider="custom", ...)` in `setup_wizard.py`) writes this same shape after probing `GET /v1/models` on the URL you give it.

#### Token Usage / `reasoning_tokens` Capture (streaming gotcha)

`LiteLLMAdapter` (`src/core/intelligence/adapters/litellm_adapter.py`) requests `stream_options: {"include_usage": True}` so LM Studio/litellm return a `usage` object — `prompt_tokens`, `completion_tokens`, `total_tokens`, and `completion_tokens_details.reasoning_tokens` — at the end of every streaming turn. This feeds the `TokenUsageTicker` in the web UI.

**The chunk carrying `usage` is not guaranteed to be the same chunk that carries `finish_reason`.** In practice LM Studio sends it as a *separate trailing chunk one tick after* `finish_reason="stop"`. A hybrid-streaming loop that does `if choice.finish_reason: break` immediately will exit before that trailing chunk is ever read, and `GenerateResponse.usage` silently comes back `None` — no error, no log, it just looks like usage was never sent. This happened in production for a while before being caught (see `git log --oneline -- src/core/intelligence/adapters/litellm_adapter.py` around the "capture usage/reasoning_tokens dropped by early stream break" fix).

If you touch the streaming loop in `generate()`: keep listening for a few chunks after `finish_reason` fires (the code caps it at 3 to avoid hanging on providers that never send a usage chunk) instead of breaking immediately. `tests/unit/test_litellm_usage_capture.py` reproduces the exact ordering with a mocked stream and is wired into CI (`.github/workflows/evals.yml`) — it will fail if this regresses. When debugging "usage isn't showing up," verify capture directly against a live LM Studio instance (`GenerateResponse.usage` from `adapter.generate(...)`) before assuming the Brain→Limb→UI plumbing is broken — that plumbing (`ui_state_syncer.py`, `brainSocket.ts`, `store.ts`, `TokenUsageTicker.tsx`) has no bug detection of its own; it just renders whatever the adapter gives it.

## Featured Model: Qwen 3.6
Qwen 3.6 (and later) is highly recommended for local tool-calling. It demonstrates:
- Strong instruction following for system commands.
- Consistent JSON output for tool arguments.
- Fast inference on consumer hardware.

## Configuration (config.yaml / preferences.yaml)

Local models are configured under `local_models` in `config/config.yaml` (defaults) and overridden per-machine in `config/preferences.yaml` — there is no `.env`-based toggle; `LOCAL_LMSTUDIO_ENABLED`/`LOCAL_OLLAMA_ENABLED`/`LOCAL_LMSTUDIO_URL`/`LOCAL_OLLAMA_URL` env vars from older docs are not read anywhere in the code.

```yaml
local_models:
  use_local: true          # master on/off switch for local inference
  providers:
    lmstudio:
      base_url: http://localhost:1234/v1
      api_key: lm-studio
      enabled: true
    ollama:
      base_url: http://localhost:11434/v1
      api_key: ollama
      enabled: false
  active_model: google/gemma-4-26b-a4b-qat   # bare string = always resolves via providers.lmstudio
```

`active_model` as a bare string only ever routes through `providers.lmstudio` (see the Ollama/llama.cpp/vLLM sections above) — use the dict form (`{name, provider, base_url, api_key}`) to point at any other backend. The Settings model picker and the setup wizard's backend chooser (#499) both write the dict form for you; you rarely need to hand-edit this.

## UI Integration

The **ModelSelector** in the Chad GUI provides a unified view of all available models:
- **Cloud Models**: Gemini 1.5, 2.0, 3.x Flash.
- **Local Models**: Discovered on the fly.
- **Badges**:
  - `🛠️`: Tool calling verified.
  - `⚠️`: No tool support detected (context-only).

## Technical Details

- **Adapters**: `src/core/intelligence/adapters/openai_adapter.py` (streaming + dirty-parser fallback), `src/core/intelligence/adapters/litellm_adapter.py` (hybrid streaming, thinking injection, profile-driven models).
- **Provider capability flags**: `src/core/intelligence/provider_capabilities.py` — see [above](#provider-capability-flags-issue-498).
- **Provider lifecycle** (load/unload/VRAM budget): `src/core/intelligence/providers/` — `LMStudioLifecycle`, `OllamaLifecycle`, `StaticEndpointLifecycle` (llama.cpp/vLLM), resolved by `resolve_provider_name()`.
- **Discovery**: `src/core/intelligence/local_model_discovery.py`.
- **Config resolution**: `local_models.active_model` → adapter is built in `ModelRouter._ensure_local_model()` (`src/core/intelligence/model_router.py`) via `ConfigManager.get_local_model_config()` (`src/shared/config_manager.py`).
- **Setup wizard backend chooser** (#499): `src/infrastructure/services/setup_wizard.py` — probes a backend, lists its models, and writes the config shapes shown above.

## Verification status (issue #500)

- ✅ LM Studio: exercised continuously — this is Chad's default dev/prod backend.
- ✅ Ollama: live multi-tool turn verified end-to-end (`tests/manual/verify_ollama_e2e.py` against `qwen3.5:4b` on a real Ollama instance, `:11434`, no LM Studio running) — tool call emitted, result fed back, model produced a correct natural-language answer.
- ✅ llama.cpp `server` (representative for `StaticEndpointLifecycle`, which vLLM also resolves to): same script run against a real `llama-server` instance serving `Qwen3.5-9B` via config only (`provider: static`, arbitrary `base_url`/`api_key`) — no code changes, tool call + follow-up both correct. A 1.5B coder-tuned GGUF was tried first and failed to emit native `tool_calls` (its chat template has no tool-call format), which is a model/template limitation, not a `StaticEndpointLifecycle` bug — swapping to a tool-tuned model fixed it, so pick models with tool-calling support regardless of backend.

Verification script: `tests/manual/verify_ollama_e2e.py` drives `LiteLLMAdapter` directly (same class production uses via `ModelRouter`) against a given `--base-url`/`--model`, independent of `config/preferences.yaml` — safe to run without touching a live server's config.

The model picker lists cloud catalogue entries only when their provider has configured credentials. A configured key does not guarantee validity or available credit. Without credentials, the picker explains that a personal API key is required. Local discovery runs once per picker mount, with explicit refresh for subsequent attempts; cached models remain selectable during refresh. Startup uses an indeterminate activity bar because readiness has no measurable percentage.

Hardware-based model and LM Studio settings recommendations are tracked in [issue #5](https://github.com/MatthewLoyed/Chad/issues/5).
