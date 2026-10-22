# Chat Mode Optimization & Benchmark

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Overview
Chat Mode is designed for fast, direct, read-only conversational Q&A and information retrieval. Unlike Agent Mode (which handles multi-turn planning, subagent delegations, shell execution, and state mutations), Chat Mode is streamlined for sub-second responses while preserving access to personal context and read-only tools.

---

## Key Architecture & Design

### 1. Single Source of Truth for Selector Model
- Centralized default selector fallback to `ConfigManager.get_llm_selector_model()` (`DEFAULT_LLM_SELECTOR_MODEL = "qwen2.5-3b-instruct"`), eliminating hardcoded selector model names across commands, adapters, lifecycle hooks, and supervisor files.

### 2. Read-Only Toolset (Single-Hop Routing)
Chat Mode provides 13 fast, read-only tools:
- **Web & Research**: `web_search`, `search_browser_history`
- **Google Workspace**: `google_drive_search`, `google_drive_list_folder`, `google_drive_read_file`, `get_calendar_events`, `search_gmail`, `read_gmail`, `search_contacts`
- **Finances**: `get_financial_summary`, `get_recent_transactions`, `get_investment_portfolio`, `get_recurring_subscriptions`

**Routing Rules**:
- **Round 0**: In `conversational_node`, the model is bound to the read toolset.
- **Single-Hop Execution**: If the model emits a tool call, `ToolNode` executes the tool and records `chat_tool_rounds += 1`.
- **Round 1**: `conversational_node` receives the `ToolMessage` result without tools bound, synthesizing the final answer directly with no multi-hop tool loop.

### 3. Deliberation / Reasoning Disabled (`thinking_level = "off"`)
- In Chat Mode, `pipeline.py` and `conversational_node.py` enforce `thinking_level = "off"` and `thinking_enabled = False`.
- In `litellm_adapter.py` and `openai_adapter.py`, `thinking_level == "off"` explicitly injects `reasoning_effort: "none"`, `extra_body.reasoning_effort: "none"`, and `chat_template_kwargs: {"enable_thinking": False}`, turning off local model thinking.
- This cuts out the 10s–15s internal thinking delay while maintaining full decision-making accuracy.

### 4. UI Toolkit Carousel Hidden
- `self._emitter.tools_menu(...)` in `pipeline.py` is gated with `if self.turn_mode != "chat":`, suppressing the 13-tool carousel chip widget in Chat Mode.

### 5. Context Bloat Elimination
- In `tool_preparation.py`, multi-month session history RAG is skipped for Chat Mode (`session_client=None`), preventing prompt bloat and dropping TTFT prefill latency from ~8.2s down to < 0.5s.
- Always-on personal context (Core Profile facts, Knowledge Graph relational entities, upcoming calendar events, recent activity) is retained intact.

---

## Benchmark Results (Local 27B Model in LM Studio)

### 1. Speed & Latency Comparison

| Query | Category | Latency (Thinking: OFF) | Latency (Thinking: MINIMAL) | Speedup |
| :--- | :--- | :---: | :---: | :---: |
| `"What is 25 * 4?"` | Direct Q&A | **0.46s** | **8.59s** | ~18x |
| `"Give me a one-sentence tip for productive deep work."` | Concise Q&A | **0.60s** | **17.83s** | ~30x |
| `"where do i live"` | Core Profile fact | **0.73s** | **2.80s** | ~3.8x |
| `"what is that one yt vid about bakugo i watched recently"` | Browser history | **0.75s** | **4.89s** | ~6.5x |
| `"whats my mom and fiances email"` | Contacts / Graph | **2.83s** | **3.92s** | ~1.4x |
| `"update me on my finances"` | Financial snapshot | **0.52s** *(dispatch)* | **13.83s** *(dispatch)* | ~26x |

### 2. Tool-Calling Behavior
- **Parallel Dispatch**: For *"whats my mom and fiances email"*, Thinking OFF emitted parallel tool calls `search_contacts({'query': 'Mom'})` and `search_contacts({'query': 'Jordan Example'})` in 2.83s, whereas Thinking MINIMAL took 3.92s and only dispatched a single call.
- **Financial Snapshot**: For *"update me on my finances"*, `get_financial_summary({})` was triggered in 0.52s and returned exact balances:
  - Net Worth: $19,681.87
  - Total Cash: $14,040.35 (SoFi accounts)
  - Investments: $5,768.35 (Fidelity Roth IRA)
  - Debt: $126.83 (Discover it Chrome card)

---

## Verification
- Unit test suite: `tests/unit/test_chat_mode_flow.py` and `tests/unit/test_chat_mode_read_tools.py` (15/15 passed).
- Live evaluation: `scripts/benchmark_chat_mode.py` and `scripts/test_complex_chat_queries.py`.
