# Implementation Plan: LiteLLM Standardization

## Phase 1: Setup & Dependencies
- [x] Add `litellm` to `requirements.txt`.
- [x] Install `litellm` in the local environment.

## Phase 2: Core Adapter Implementation
- [x] Create `src/core/intelligence/adapters/litellm_adapter.py`.
    - [x] Implement `GenerateResponse` normalization.
    - [x] Implement `generate()` with streaming support.
    - [x] Add "Thinking/Reasoning" extraction from stream chunks.
    - [x] Add "Dirty Parser" fallback for tool calls.
    - [x] Implement `convert_tool_schemas` (OpenAI format).

## Phase 3: Model Layer Implementation
- [x] Create `models/litellm_model.py`.
    - [x] Implement `BaseModel` interface.
    - [x] Initialize `LiteLLMAdapter` based on config.
    - [x] Implement `execute_text` and `stream_audio` (mocked/text-only).

## Phase 4: Integration
- [x] Update `src/core/intelligence/model_router.py`.
    - [x] Add `litellm` to supported model types.
    - [x] Ensure proper instantiation of `LiteLLMModel`.
- [x] Update `config/config.yaml`.
    - [x] Configure `agentic` and `text` categories to use `type: litellm`.
    - [x] Update model names to LiteLLM format (e.g., `gemini/gemini-2.0-flash`).

## Phase 5: Verification & Cleanup
- [x] Test Gemini integration via LiteLLM.
- [x] Test LM Studio (local) integration via LiteLLM.
- [x] Verify tool-calling works correctly for both.
- [ ] (Optional) Remove legacy logic if no longer needed.
