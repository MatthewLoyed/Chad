# 🌐 Browser Automation Guide

Chad integrates **Playwright via MCP** to provide high-fidelity web automation. This allows the assistant to interact with websites as a human would—clicking, typing, and navigating through complex Single Page Applications (SPAs).

## Browser progress controller (experimental)

`orchestration.browser_controller_enabled` in `config/config.yaml` defaults to
`false`. Set it to `true` and restart the dev brain to test the new controller;
set it back to `false` and restart to return to the original specialist loop.
Keep autonomous actions disabled in the GUI during manual comparisons.

The controller requires one structured action at a time, keeps the original
objective and a compact current observation, and tracks repeated states separately
from tool errors. It limits recovery attempts, logical actions and execution time.
Human approval waits do not consume execution time. The existing approval gate
and Stop cancellation path remain in use.

`browser_scroll(target="e42")` scrolls the observed panel or a scrollable ancestor.
The controller also discovers a visible scroll container when no target is given.
Results include position and boundary evidence plus the resulting snapshot.
Movement alone never establishes task completion.

For an explicitly supplied Gemini `/app/<conversation>` URL and a request to copy
the last response, the controller now uses a bounded workflow: navigate, click the
observed “Scroll to bottom” button, inspect the conversation bottom and completed
last `model-response`, then capture its `.markdown` body directly. Loading waits
are bounded; unsupported layouts stop without a capture. Every action still uses
the existing gated ToolNode. This Gemini adapter does not apply to arbitrary sites
or share pages. It requires no model decisions for the supported path.

Success is a saved exact message and a validated `content_id`; creating a Doc is
optional. The general model loop requires only action and arguments. Runtime
state owns progress counters, blocks more scrolling after a verified bottom, and
uses current page evidence instead of model-authored memory. Snapshot references
such as `f5e611` are supported as well as `e42`.

The chat displays a replaceable progress card with the milestone, immediate
subgoal, recovery reason, counts and terminal status. This is public task status;
the controller's compact memory is not streamed as reasoning.
The compact card stays visible outside collapsed subagent trace panels. Large
browser results clip text inside their JSON envelopes so success and scroll
metadata remain parseable.

For exact text transfer, `browser_capture_text` stores one response body and returns
a scoped `content_id`. The handoff preserves validated references through sanitized
tool results. `google_drive_create_doc(content_id=...)` reads the stored body and
checks the created document, keeping the text outside model context.

Run `python -m evals.browser_progress_benchmark --variant both --repeats 5
--legacy-prompt-file <saved-original-effective-prompt>` from the repository root
with the local model loaded. The runner uses fictional pages in isolated headless
browser contexts, the production graph/tool pipeline, alternating variants and
per-trial source/prompt snapshots. It does not access signed-in accounts. Generate
the report with `python -m evals.browser_progress_report <comparison.json>
--output <report.md>`. Model fixtures and a real signed-in Gemini → Docs smoke
test serve different purposes; both are needed before broad rollout.

## 🚀 Key Capabilities

- **Stateful Navigation**: Maintain a browser session across multiple tool calls.
- **Form Interaction**: Fill out inputs, select dropdowns, and submit forms.
- **Dynamic Content**: Wait for elements to appear or for the page to network-idle.
- **Vision-Lite Snapshots**: Capture the accessibility tree (ARIA) of the page to "understand" structure without the token cost of raw HTML.
- **Scripting**: Execute custom JavaScript within the browser context.

## 🧠 Technical Architecture: Snapshot Pruning

Browser snapshots can be massive (hundreds of kilobytes), which quickly exhausts LLM context windows and increases latency. Chad uses a custom **Snapshot Pruner** algorithm:

1.  **Selective Node Retention**: It iterates through the accessibility tree and keeps only "high-value" nodes:
    *   **Interactive Elements**: Buttons, links, inputs, checkboxes, etc.
    *   **Content Containers**: Headings and major text blocks.
    *   **Structural Context**: Parent nodes required to preserve the document hierarchy.
2.  **Noise Elimination**: It strips out generic containers (`div`, `span`, `generic`) that do not contain interactive children or significant text.
3.  **Token Savings**: This process typically reduces the snapshot size by **75% to 90%**, allowing models like Qwen or Gemini to handle complex pages within their standard context limits.

## 🛠 Configuration

The browser is configured in `config/config.yaml`:

```yaml
mcp_servers:
  playwright:
    command: "npx"
    args: 
      - "-y"
      - "@playwright/mcp"
      - "--output-dir"
      - ".playwright-snapshots"
    env:
      CHAD_HEADLESS: "true"
```

### Headless Mode
By default, the browser runs in **headless mode** to ensure maximum performance in the background. If you need to see the browser for debugging, you can toggle the `CHAD_HEADLESS` environment variable in your `.env` file.

## 📂 Artifacts
Every time Chad navigates to a new page or takes a snapshot, a YAML representation of the accessibility tree is saved to:
`./.playwright-snapshots/`

These files are ignored by git (via `.gitignore`) but are extremely useful for manual inspection of what the AI is "seeing."

## 🔒 Security Protocol
To protect against **Prompt Injection** from untrusted web content, Chad's ReAct loop automatically injects a security addendum into the system prompt whenever browser data is in the conversation history. This instructs the model to treat all web text as data, not instructions. Every tool output (browser or otherwise) also passes through `sanitize_tool_output` (`src/core/orchestration/agents/agent_utils.py`), which strips/neutralizes known injection phrases and tags content with a `[TOOL_DATA]` marker before it enters the message history. This applies equally to on-screen/DOM text read today and to UIA-read desktop text once #278 lands — neither is ever treated as instructions, regardless of who or what it appears to address ("AI, please click...", "attention assistant...", etc. are explicitly pattern-matched and stripped).

## 🛡️ Computer-use safeguards (issue #279)
Posture: **read-only by default, confirm writes.** Implemented in `src/core/orchestration/browser_safety.py`, wired into `MCPExecutor._browser_safety_gate` (`executor_strategy.py`) for every `browser_*` MCP call, before the loop-breaker check:

- **Write-action approval cards**: state-changing calls (`browser_file_upload`, `browser_handle_dialog`, `browser_select_option`, `browser_drag`, `browser_fill_form`, `browser_evaluate` always; `browser_click`/`browser_type`/`browser_press_key` when the element description, typed text, or key matches a write-signal keyword list, e.g. "submit", "buy", "checkout", "delete", or a submitting Enter key) pause via the same executor-agnostic `evaluate_approval_gate` (`src/core/orchestration/approval_gate.py`) that backs `send_email`/`execute_sql`/etc. — rendered through the existing `ApprovalCard`/`ToolApprovalPanel`, `TOOL_APPROVAL_REQUEST`/`RESPONSE` protocol, and full `conversation_id`/`guardConversation` session-isolation guarantees. No new frontend code was needed.
- **Read-only default**: navigation, scrolling, snapshot/screenshot, console/network reads, and tab/resize/wait calls (`READ_ONLY_TOOLS` in `browser_safety.py`) never pause, regardless of site trust.
- **Per-site allowlist**: `config/config.yaml` → `tools.browser_write_allowlist` (domains + subdomains treated as trusted). Off-allowlist sites get **stricter treatment**: any `browser_click`/`browser_type`/`browser_press_key` pauses for approval, not just ones that already look like a write — element-description heuristics aren't trusted enough on a page with no prior trust signal.
- **Kill-switch / action budget**: `tools.browser_max_actions_per_task` (default 40) — a per-conversation counter in `BrowserSafetyState` blocks further browser actions once the budget is exceeded, forcing the agent to stop and report back instead of continuing to browse indefinitely.

**Propagation gap found in live testing (issue #325, fixed):** the write-classification above only pauses the *tool call* — it does nothing by itself if the graph executing that tool has no concept of "pause and wait for a human." `browser_agent` (like every specialist) is built by `create_specialist_agent` (`src/core/orchestration/agents/factory.py`), which used to call the stock `langgraph.prebuilt.create_react_agent`. That prebuilt agent's routing only checks whether the model's last message has tool_calls; it has no `_pending_approval` awareness, so a gated write's pause JSON just got fed back into the specialist's own model and the ReAct loop continued — no ApprovalCard ever reached the UI, and the model wasn't actually stopped. Fixed by:
- `src/core/orchestration/agents/specialist_graph.py` (new) — a small custom two-node (agent ↔ tools) graph replacing `create_react_agent`, reusing `nodes/tool_node.py`'s `make_tool_node` (the same pending-approval detection already proven for the supervisor's own graph) so the tools node can set `pending_approval` in state and a conditional edge exits immediately instead of looping.
- `src/core/orchestration/handoffs.py` — `subagent_node`/`handoff_fn` now check the subgraph's `result.get("pending_approval")` after `ainvoke()` and forward it as the same JSON envelope the supervisor's own tool node expects, instead of collapsing it into a natural-language summary.
- A related bug caught while fixing this: `SanitizedToolNode` (`agent_utils.py`) only overrode the sync `invoke()`; `ToolNode.ainvoke()` calls `self.afunc(...)` directly (bypassing `invoke()` entirely) whenever an async func is registered, which is always true — so `sanitize_tool_output` never actually ran under async execution (the only execution path this codebase uses). Fixed by overriding `ainvoke` too, and by exempting internal control-signal JSON (`_pending_approval` etc.) from the `[TOOL_DATA]` prefix so `json.loads()` downstream still parses it.

## 🤖 browser_agent (DOM-first specialist, epic #274 / issue #287)

`browser_agent` is the one specialist subagent allowed to directly bind and execute the raw
Playwright MCP tools (`schema_builder.py` `get_tools_by_domain("browser")` allow-lists the
`playwright` category — every other subagent can only discover MCP tools via `call_mcp_tool`,
never execute them directly). It drives a real Chrome instance on a **dedicated automation
profile** (`config.yaml` → `mcp_servers.playwright.--user-data-dir`, `%LOCALAPPDATA%\ChadBrowser\UserData`),
separate from the user's real browser. Runtime enforcement in `src/shared/chad_browser_profile.py`
rewrites any attempt to attach Playwright to `Google\Chrome\User Data` (that was locking daily
Chrome and orphaning MCP processes). MCP disconnect/cleanup kills `@playwright/mcp` process
trees so chrome children do not zombie. Seed with `scripts/maintenance/seed_chrome_profile.ps1`
(backs up ChadBrowser first; refuses token-downgrade unless `-Force`).

- **`open_browser` is deliberately excluded** from `browser_agent`'s tools. It calls
  `webbrowser.open()`, which launches the user's real system-default browser — binding it
  alongside the Playwright tools let the model sometimes pop the user's own Chrome instead of
  the sandboxed automation profile.
- **`browser_scroll`** (`src/tools/web/browser_scroll.py`) is a native tool, not part of
  `@playwright/mcp`'s 23 tools — that MCP server has no scroll primitive, which blocked the
  agent on infinite-scroll listing pages (AutoTrader, Zillow, etc.). It composes
  `browser_evaluate` (scrollBy) → `browser_wait_for` (let lazy content settle) →
  `browser_snapshot` into one call.
- **Loop-breaker** (`MCPExecutor` in `executor_strategy.py`): local models were observed
  retrying an *identical* failing Playwright call (same tool + same args) 10+ times in a row
  without ever re-checking page state. `MCPExecutor` now tracks a per-signature consecutive-
  failure counter for `browser_*` tools and blocks the 3rd identical repeat with a message
  telling the model to re-snapshot or change approach. Repeats of a call that *succeeded*
  (e.g. pressing PageDown twice to scroll further) are never blocked.
- **Model selection**: the browser shares the supervisor's active model, avoiding
  model swaps during delegation. Stale per-machine browser pins are ignored by
  `src/core/orchestration/agents/factory.py`. Record the actual model, quantization,
  loaded context and thinking settings when comparing runs; changing models is a
  separate experiment from changing the controller.
- **Verification harness**: `evals/browser_playwright_probe.py` talks straight to the
  Playwright MCP server + the pinned model via `LiteLLMAdapter`, bypassing the supervisor/
  LangGraph/subagent machinery entirely. Use it to isolate "is it the harness (tool binding/
  routing)?" from "is it the model?" before touching orchestration code.
- **Separate hybrid DOM probe (historical Gemma experiment):** `evals/browser_hybrid_probe.py` — stable
  `data-chad-ref` controls, READABLE TEXT, honest OK/FAIL/NO_EFFECT/BLOCKED feedback, hard
  loop-breaker, optional `screenshot` / `select`. Model: `google/gemma-4-26b-a4b-qat`.
  **Proved (2026-07-03):** HN read, AutoTrader.ca listing price, GTA VI character names
  (Jason, Lucia), ExampleBiz owners (Jordan Sample, Alex Example). **Biggest unlock: feedback
  quality**, not a new CUA. Writeup: `docs/features/BROWSER_HYBRID_PROBE.md`.
- **Fara-7B vision spike (issue #288) — complete, not promoted:** `evals/browser_fara_probe.py`
  screenshot-native CUA. Official `fara-cli` also hallucinated finals. Do **not** ship Fara as
  Path B. See `docs/features/FARA_SPIKE.md`.
- **Known gap**: local model inference in LM Studio has been observed to report
  `Model unloaded` mid-generation during long agentic runs (20+ turns), independent of which
  model is loaded — not yet root-caused. If a live browser_agent run dies with a LiteLLM
  `APIConnectionError: Model unloaded`, this is the likely cause, not a harness bug.
