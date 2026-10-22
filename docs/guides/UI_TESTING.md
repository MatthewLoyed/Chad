# UI Testing Guide

This guide is the **definitive standard** for testing and debugging the Chad React frontend. There are three testing tiers that serve different purposes — understanding which to reach for first saves hours of debugging.

---

## The Three Tiers (Read This First)

```
┌─────────────────────────────────────────────────────────────────┐
│  TIER 1 — Storybook (port 6006)                                 │
│  "Does the component render correctly given valid data?"         │
│                                                                  │
│  ✅ Use when: building or modifying a component                  │
│  ✅ Use when: visually checking every state of a component       │
│  ✅ Use when: catching regressions after a style change          │
│  ❌ Does NOT test: how data actually arrives from the backend    │
└─────────────────────────────────────────────────────────────────┘
         │
         │  If component looks right here but wrong in prod
         │  → The bug is in the DATA PIPELINE, not the component
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  TIER 2 — Web-UI + window handlers (port 5174)                  │
│  "Does the full event pipeline work end-to-end?"                 │
│                                                                  │
│  ✅ Use when: something works in Storybook but not in prod       │
│  ✅ Use when: simulating exact backend event sequences           │
│  ✅ Use when: reproducing a production bug with real store state │
│  ❌ Does NOT isolate: individual component states                │
│  ❌ Does NOT catch: service-worker routing, dead client stubs,   │
│     real auth/token flows, real network behavior                 │
└─────────────────────────────────────────────────────────────────┘
         │
         │  If the pipeline is provably fine (Tier 2 green) but the
         │  real app still misbehaves — or the bug is about a whole
         │  user-visible flow (auth, download, artifact render), not
         │  one component's data — you're not looking at a component
         │  bug anymore.
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  TIER 3 — Claude Browser against a real running Brain           │
│  "Does the actual user-facing flow work, end to end, for real?"  │
│                                                                  │
│  ✅ Use when: verifying a fix for a user-reported bug            │
│  ✅ Use when: testing anything that crosses process/origin       │
│     boundaries — auth, service workers, download buttons,        │
│     WebSocket reconnect, artifact preview iframes                │
│  ✅ Use when: you need proof a fix works, not just a plausible   │
│     code-reading argument                                        │
│  ❌ Slower and more expensive than Tiers 1-2 — reach for it      │
│     after, not instead of, unit/eval coverage                    │
└─────────────────────────────────────────────────────────────────┘
```

### Why components can "look fine in isolation but break in production"

Storybook feeds a component clean, hand-crafted props. Production feeds it data that has travelled through the entire pipeline:

```
Python backend
  → EventBus.publish(ExecutionPlanEvent)
    → EventBridge._on_event_execution_plan()         ← was missing! (the bug)
      → MainWindow.push_thinking_event({type: 'execution_plan', ...})
        → window.handleThinkingEvent(event)           ← JS handler in App.tsx
          → addThinkingEvent(event)                   ← Zustand store action
            → AIResponseRenderer reads currentThoughts
              → PlanCard renders                      ← Storybook only tests this bit
```

If **any link** in that chain is broken, the component never receives data. Storybook can't catch that — it skips directly to the last step.

**The plan card bug** was a perfect example: PlanCard rendered flawlessly in Storybook, but `EventBridge` had no subscription to `ExecutionPlanEvent`, so the data never reached the component in production.

---

## Tier 1 — Storybook Component Gallery

### Starting Storybook

```
preview_start "storybook"
```
→ runs on **port 6006** via `.claude/launch.json`

Or from the terminal:
```powershell
cd src\clients\web
npm run storybook
```

### Story Catalogue

| Component | Location | Stories |
|---|---|---|
| `PlanCard` | `src/stories/PlanCard.stories.tsx` | AwaitingApproval, InProgress, AllCompleted, WithFailure, HistoryCollapsed, LongPlan, SingleStep, NoGoal |
| `ThinkingWidget` | `src/stories/ThinkingWidget.stories.tsx` | ActiveStreaming, ActiveWithThoughts, CompletedClosed, HistoryEntry, DeepReasoning |
| `MarkdownMessage` | `src/stories/MarkdownMessage.stories.tsx` | PlainText, RichMarkdown, WithCodeBlock, GFMTable, MultipleCodeBlocks, LongCollapsible |
| `ToolStackBubble` | `src/stories/ToolStackBubble.stories.tsx` | SingleFilesystem, StackedBrowser, MixedStatus, DelegationStack, LargeStack |
| `ArtifactCard` | `src/stories/ArtifactCard.stories.tsx` | TextFile, PythonScript, Spreadsheet, PDF, LongFilename |
| `ChatStream` | `src/stories/ChatStream.stories.tsx` | Empty, SimpleConversation, WithExecutionPlan, WithToolCalls, ActiveThinking |

### Navigating stories via preview_eval

```js
// Go to a specific story iframe (for DOM inspection)
window.location.href = '/iframe.html?id=data-display-plancard--awaiting-approval&viewMode=story';
```

URL pattern: `/iframe.html?id=<title-kebab>--<story-name-kebab>&viewMode=story`

Examples:
- `data-display-plancard--in-progress`
- `data-display-thinkingwidget--deep-reasoning`
- `layout-chatstream--with-execution-plan`

### Adding a new story

1. Create (or add to) `src/clients/web/src/stories/<ComponentName>.stories.tsx`
2. Follow the existing pattern — `Meta`, `StoryObj`, fixture data, named exports
3. Storybook HMR picks it up instantly — no restart needed
4. Add the story to `all-stories.visual.ts` to include it in visual regression

**Story template:**
```tsx
import type { Meta, StoryObj } from '@storybook/react';
import MyComponent from '../components/path/MyComponent';

const meta = {
    title: 'Category/MyComponent',
    component: MyComponent,
    tags: ['autodocs'],
    parameters: { layout: 'padded' },
} satisfies Meta<typeof MyComponent>;

export default meta;
type Story = StoryObj<typeof meta>;

export const DefaultState: Story = {
    args: { prop: 'value' },
};

export const AlternateState: Story = {
    name: 'Alternate State',
    args: { prop: 'other' },
};
```

---

## Tier 2 — Web-UI Pipeline Integration Testing

### Starting the web-ui

```
preview_start "web-ui"
```
→ runs on **port 5174** via `.claude/launch.json`

The backend is **not required** for injecting test data — the "Connecting to Brain..." banner is harmless. Start the backend only if you need to test WebSocket auth or server-side state:

```powershell
$env:PYTHONPATH = "C:\path\to\AIAssistant\.claude\worktrees\<name>"
$env:CHAD_DAEMONS_ENABLED = "false"
& "C:\path\to\AIAssistant\.venv\Scripts\python.exe" `
    "<worktree-path>\src\remote\server.py"
```

### Window Handler Reference

These are the exact same calls the Python backend makes via pywebview JS evaluation. Using them in `preview_eval` simulates the full frontend pipeline.

| Handler | Simulates | Backend source |
|---|---|---|
| `window.handleThinkingEvent(event)` | Any thinking event (thought, tool_start/end, execution_plan, speech) | `event_bridge.py` → `push_thinking_event` |
| `window.addAIMessageToChat(text, conv?, id?, meta?)` | Turn complete, message committed to history | `event_bridge.py` → `on_turn_complete` |
| `window.appendStreamToken(delta)` | Live streaming token | `event_bridge.py` → `on_caption_update` |
| `window.appendThinkingToken(delta)` | Live thinking token | `event_bridge.py` → thinking stream |
| `window.addFilePlanToChat(jsonStr, token)` | `propose_file_plan` tool result | `propose_file_plan.py` |
| `window.handleStateUpdate(state)` | Status change (ready/thinking/executing) | `event_bridge.py` → `on_state_change` |
| `window.handleTokenUsage(usage)` | Token telemetry row | `event_bridge.py` → `on_token_usage` |

### Injection Examples

#### Simulate an execution plan (production pipeline path)
```js
// This is exactly what EventBridge._on_event_execution_plan broadcasts
window.handleThinkingEvent({
  type: 'execution_plan',
  plan: [
    { id: 's1', description: 'Research the problem', status: 'completed' },
    { id: 's2', description: 'Implement the fix', status: 'running' },
    { id: 's3', description: 'Write tests', status: 'pending' },
  ],
  goal: 'Fix the plan card rendering bug',
  plan_id: 'test-plan-1',
  timestamp: Date.now()
});
window.handleThinkingEvent({ type: 'speech', text: "Here's the plan above." });
window.addAIMessageToChat("Here's the plan above.");
```

#### Simulate a full tool call sequence
```js
window.handleThinkingEvent({ type: 'tool_start', name: 'web_search', correlation_id: 'c1', params: { query: 'storybook vite 8' }, timestamp: Date.now() });
window.handleThinkingEvent({ type: 'tool_end', name: 'web_search', correlation_id: 'c1', success: true, result: '5 results', timestamp: Date.now() + 1200 });
window.handleThinkingEvent({ type: 'speech', text: 'Found the docs.' });
window.addAIMessageToChat('Found the docs.');
```

#### Simulate streaming
```js
window.handleStateUpdate('thinking');
["I ", "found ", "the ", "issue! ", "The EventBridge ", "was missing ", "a subscription."].forEach(t => window.appendStreamToken(t));
window.addAIMessageToChat("I found the issue! The EventBridge was missing a subscription.");
```

#### File plan approval card
```js
window.addFilePlanToChat(JSON.stringify([
  { id: 's1', description: 'Move report.pdf to /docs', status: 'pending' },
  { id: 's2', description: 'Delete old drafts/', status: 'pending' },
]), 'approval-token-xyz');
```

---

## Tier 3 — Live Browser Testing Against a Real Brain

Storybook and Tier 2 both test the frontend in isolation, fed synthetic data. Neither one runs a real WebSocket auth handshake, a real service worker, or a real click on a real anchor tag. Some bugs only exist in that gap — they were invisible to both tiers and would have shipped:

- A PWA service worker's `navigateFallbackDenylist` didn't cover `/files/{token}`, so every artifact preview silently loaded Chad's own cached app shell instead of the generated file (infinite loading skeleton, no error anywhere).
- A browser-mode API stub (`download_artifact`) existed just to report "unavailable," which made a truthy-check upstream think a real handler was present — the Save button did a full no-op, no console error, no failed network request, nothing.

Both are the kind of bug where reading the code gives you a *plausible* fix, but only clicking the real button in a real browser against the real backend tells you whether it's actually fixed.

### The method

1. **Run the frontend from source, not from `dist/`.** Start a Vite dev server (`npm run dev`) from the worktree you're editing — HMR means every edit is live immediately. Do **not** hand-copy a `dist/` build into the live prod checkout to "test the real thing" — it's fragile (a stray `npm run build` invocation, of your own or something else's, can silently clobber it) and it's editing the thing you're supposed to be protecting. This bit us mid-session and briefly left prod's `dist/` deleted; don't repeat it.
2. **Point that dev server at the real backend.** `transportConfig.ts`'s `defaultServerUrl()` derives the Brain URL from `window.location.hostname` on port 8767 — so a dev server on `localhost:5173` talking to a Brain on `localhost:8767` is still same-machine/loopback as far as the Brain's auth is concerned. You get real backend behavior (real auth, real tool calls, real file registry) with zero risk to the backend's own served static assets.
3. **Drive it with the Browser tool**, not raw `curl` alone — curl proves the server returns the right bytes; it does *not* prove a service worker won't intercept the request, or that a client-side stub won't swallow a button click before any request is even made. Both bugs above were invisible to curl and only visible in an actual browser tab.
4. **Verify by observable side effect, not by absence of error.** "No console error" is not proof — the `download_artifact` bug produced zero errors and zero requests. Better checks: did a new entry appear in `read_network_requests`? Did the page title change (confirms real content, not the cached app shell)? Did `window.pywebview.api.<fn>` actually have the type you expect?
5. **Treat this as a smoke test for the fix, not a replacement for eval/unit coverage.** It's slow (real turns take real model inference time) and it's the most expensive tier — reach for it to confirm a specific user-facing flow works end to end, not as your default dev loop.

### Worked example (artifact render + download, issue #528)

```
1. Confirm the real backend is up: curl -s localhost:8767/health
2. Start the frontend from source, pointed at it:
     preview_start name: "web-dev"  (npm run dev in src/clients/web, port 5173)
3. Drive a real turn through the Browser tool: type a prompt that makes
   code_agent build an artifact, wait for it to finish (real inference time).
4. Open the artifact preview pane for real — click it, don't assume.
   Check read_network_requests for the /files/{token} GET and its response.
   If the iframe's title becomes the artifact's title (e.g. "Test Cafe"),
   not the app's own title ("Chad AI - Assistant"), the content is real —
   not the cached SPA shell a service worker substituted in.
5. Click the actual Save/download button. A real download bug (dead stub)
   produces *zero* new network requests — check read_network_requests
   before and after the click, don't just eyeball the screen.
```

---

## Visual Regression Testing

Pixel-perfect screenshot comparison against committed baselines. Any visual change shows up as a PNG diff in GitHub PRs.

### Commands
```powershell
cd src\clients\web

# Compare all stories against committed baselines
npm run test:visual

# Accept new/changed screenshots as the new baseline (after intentional changes)
npm run test:visual:update
```

### How it works
1. Playwright starts Storybook (or reuses a running instance)
2. Navigates to each story's iframe URL
3. Injects CSS to disable all animations (deterministic screenshots)
4. Captures a full-page PNG
5. Diffs against the baseline in `src/stories/__snapshots__/`
6. Fails if pixel diff > 0.1%

### Baseline management
- Baselines are committed to git in `src/stories/__snapshots__/`
- After intentional UI changes, run `:update` and commit the new PNGs
- GitHub shows image diffs natively — reviewers see exactly what changed visually

### Adding a new story to visual regression
In `src/stories/all-stories.visual.ts`, add an entry:
```ts
{ id: 'data-display-mycomponent--my-story', name: 'MyComponent/MyStory' },
// For async/animated stories, add waitMs:
{ id: 'data-display-mycomponent--animated', name: 'MyComponent/Animated', waitMs: 300 },
```

---

## Debugging Protocol: "Works in Storybook, Broken in Production"

When a component looks correct in Storybook but doesn't appear or renders wrong in the real app, follow this checklist:

### Step 1 — Prove the component is not the issue
Navigate to the story. Does it render correctly with the right data? If yes, the component code is fine. Move to Step 2.

### Step 2 — Test the frontend pipeline in isolation
Start `web-ui` (port 5174) and inject the **exact event the backend would send** via `preview_eval`:

```js
// Reproduce what the Python backend does
window.handleThinkingEvent({ type: 'execution_plan', plan: [...], ... });
```

**Does the component appear?**
- ✅ Yes → Frontend pipeline is fine. The backend isn't sending the event. Jump to Step 4.
- ❌ No → Something between `handleThinkingEvent` and the component is broken. Step 3.

### Step 3 — Trace the frontend pipeline
Check each link:

```js
// 1. Is the handler registered?
typeof window.handleThinkingEvent   // should be 'function'

// 2. Does the store receive it?
// Add a temporary log in App.tsx → handleThinkingEvent → addThinkingEvent

// 3. Does the store hold it?
// Check useAppStore.getState().currentThoughts after injecting

// 4. Does the renderer see it?
// The event type must be handled in AIResponseRenderer's renderNodes()
// Look for: node.type === 'execution_plan' in ChatStream.tsx
```

### Step 4 — Trace the backend pipeline
The frontend is fine. Find where the event dies on the Python side:

```
EventBus.publish(ExecutionPlanEvent)
  → Is anything subscribed? Check event_bridge.py subscribe() / limb_coordinator.py setup_event_subscriptions()
    → Is the broadcast reaching the WS? Add logger.info in _broadcast_execution_plan / _on_event_execution_plan
      → Is the WS message reaching the client? Check gui_limb.py message handler
        → Is push_thinking_event being called? Check main_window.py
```

The fix is almost always a **missing subscription** or a **wrong message type string**.

---

## Worktree Setup Notes

When working in `.claude/worktrees/*`:

- As of the Storybook install, `node_modules` is a **real directory** (npm replaced the junction during install). No junction needed.
- The `.venv` always lives at the **project root** (`AIAssistant/.venv`), never inside a worktree.
- Both `launch.json` entries (`web-ui` on 5174 and `storybook` on 6006) are pre-configured.
