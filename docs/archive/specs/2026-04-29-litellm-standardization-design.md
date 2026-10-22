# Design Doc: Standardizing Chad AI with LiteLLM

**Date:** 2026-04-29
**Topic:** Standardized Model Integration (LiteLLM)
**Status:** Approved

## Objective
Standardize Chad's AI model integration by replacing hardcoded provider-specific adapters with [LiteLLM](https://github.com/BerriAI/litellm). This will allow Chad to support 100+ model providers (OpenAI, Anthropic, Gemini, Vertex AI, Bedrock, etc.) through a single, unified interface while reducing maintenance overhead.

## Architecture

### 1. LiteLLMAdapter (`src/core/intelligence/adapters/litellm_adapter.py`)
A new adapter implementing the `AIAdapter` interface.
- **Library:** Uses `litellm` (Python SDK).
- **Functionality:**
    - Unified `generate()` method using `litellm.acompletion()`.
    - Support for streaming with real-time thinking/reasoning token extraction.
    - Multimodal support (images/documents) normalized across providers.
    - Integration of the "Dirty Parser" from `OpenAIAdapter` as a safety net for local models.
    - Rate-limiting and fallback logic handled natively by LiteLLM where possible.

### 2. LiteLLMModel (`models/litellm_model.py`)
A new model class implementing the `BaseModel` interface.
- **Purpose:** Serves as the high-level interface for the `ModelRouter`.
- **Logic:** Wraps `LiteLLMAdapter` and handles the mapping between Chad's `BaseModel` methods and the adapter's capabilities.

### 3. ModelRouter Update
- Update `src/core/intelligence/model_router.py` to support `type: litellm`.
- Modify `_create_model_instance` to handle the new model type.

## Configuration Changes (`config/config.yaml`)

Models will be configured using the LiteLLM naming convention (e.g., `provider/model_name`).

```yaml
models:
  autonomous:
    type: litellm
    model: gemini/gemini-2.0-flash
    temperature: 0.1
  conversation:
    type: litellm
    model: anthropic/claude-3-5-sonnet-20240620
    temperature: 0.9
  text:
    type: litellm
    model: openai/qwen2.5
    api_base: "http://localhost:1234/v1" # For local providers
```

## Migration Plan
1.  **Dependency:** Add `litellm` to `requirements.txt`.
2.  **Implementation:** Create `LiteLLMAdapter` and `LiteLLMModel`.
3.  **Registration:** Update `ModelRouter` to recognize the new type.
4.  **Verification:** Test with both Cloud (Gemini) and Local (LM Studio) models.
5.  **Refactor:** Once stable, mark `OpenAIAdapter` and `GeminiAdapter` as legacy.

## Specialized Cases
- **Gemini Live:** Remains as a separate specialized implementation (`gemini_live.py`) for low-latency audio streaming, as this is outside LiteLLM's standard completion scope.
