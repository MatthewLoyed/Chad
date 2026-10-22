import type { Meta, StoryObj } from '@storybook/react-vite';
import MarkdownMessage from '../components/data-display/MarkdownMessage';

const meta = {
    title: 'Data Display/MarkdownMessage',
    component: MarkdownMessage,
    tags: ['autodocs'],
    parameters: {
        layout: 'padded',
        docs: {
            description: {
                component:
                    'Renders AI response text as rich Markdown: code blocks with syntax highlighting ' +
                    'and copy/collapse, GFM tables, inline code, links (pywebview-aware), and YouTube cards.',
            },
        },
    },
} satisfies Meta<typeof MarkdownMessage>;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Stories ─────────────────────────────────────────────────────────────────

export const PlainText: Story = {
    name: 'Plain Text',
    args: {
        content: "Hello! I'm Chad, your AI assistant. How can I help you today?",
    },
};

export const RichMarkdown: Story = {
    name: 'Rich Markdown',
    args: {
        content: `
# Getting Started

Here's a quick overview of **Chad's** capabilities:

- 🧠 **Local AI** — runs entirely on your machine with LM Studio
- ☁️ **Cloud AI** — connects to Claude, GPT-4, Gemini
- 🔧 **Tool use** — files, browser, code execution, MCP servers
- 📋 **Planner** — multi-step execution with approval flows

> **Tip:** You can switch models mid-conversation using the model selector in the bottom toolbar.

---

Let me know what you'd like to work on!
`.trim(),
    },
};

export const WithCodeBlock: Story = {
    name: 'With Code Block',
    args: {
        content: `
Here's a Python function that reads a file and returns its contents:

\`\`\`python
def read_file(path: str) -> str:
    """Read a file and return its text content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

# Usage
content = read_file("config/config.yaml")
print(content[:200])
\`\`\`

You can also use inline code like \`os.path.exists(path)\` for quick checks.
`.trim(),
    },
};

export const WithGFMTable: Story = {
    name: 'With GFM Table',
    args: {
        content: `
Here's a comparison of the available AI providers:

| Provider | Mode   | Thinking | Tools | Speed  |
|----------|--------|----------|-------|--------|
| Claude   | Cloud  | ✅       | ✅    | Fast   |
| GPT-4o   | Cloud  | ❌       | ✅    | Fast   |
| Qwen3    | Local  | ✅       | ✅    | Medium |
| Gemma3   | Local  | ❌       | ✅    | Slow   |

Local models run entirely offline — no data leaves your machine.
`.trim(),
    },
};

export const WithMultipleCodeBlocks: Story = {
    name: 'Multiple Code Blocks',
    args: {
        content: `
I've refactored your function into two parts:

**Step 1** — The loader:

\`\`\`typescript
async function loadConfig(path: string): Promise<Config> {
    const raw = await fs.readFile(path, 'utf-8');
    return yaml.parse(raw) as Config;
}
\`\`\`

**Step 2** — The validator:

\`\`\`typescript
function validateConfig(config: Config): void {
    if (!config.api_key) throw new Error('Missing api_key');
    if (!config.model)   throw new Error('Missing model');
}
\`\`\`

Run both together with \`loadConfig\` then \`validateConfig\`.
`.trim(),
    },
};

export const LongCodeBlock: Story = {
    name: 'Long Code Block (collapsible)',
    args: {
        content: `
Here's the full event bridge implementation:

\`\`\`python
import asyncio
import uuid
import logging
from typing import Optional

logger = logging.getLogger("EventBridge")

class EventBridge:
    """Bridges backend EventBus events to the frontend UI."""

    def __init__(self, assistant, main_window, hud_window=None):
        self.assistant = assistant
        self.main_window = main_window
        self.hud_window = hud_window
        self.loop = asyncio.get_event_loop()
        self._active_tool_calls = {}
        self._speech_buffer = ""
        self._last_event_type = None

    def subscribe(self):
        event_bus = self.assistant.event_bus
        event_bus.subscribe(StateChanged, self._on_event_state_changed)
        event_bus.subscribe(ToolStarted, self._on_event_tool_started)
        event_bus.subscribe(ToolCompleted, self._on_event_tool_completed)
        event_bus.subscribe(ThinkingEvent, self._on_event_thinking)
        event_bus.subscribe(ReasoningEvent, self._on_event_reasoning)
        event_bus.subscribe(TranscriptEvent, self._on_event_transcript)
        event_bus.subscribe(ExecutionPlanEvent, self._on_event_execution_plan)
        logger.info("Subscribed to 19 event types")

    def _on_event_execution_plan(self, event):
        import time as _time
        self._broadcast("push_thinking_event", {
            'type': 'execution_plan',
            'plan': event.plan,
            'plan_id': event.plan_id,
            'goal': event.goal,
            'timestamp': int(_time.time() * 1000),
        })

    def _broadcast(self, method_name, *args, **kwargs):
        try:
            getattr(self.main_window, method_name)(*args, **kwargs)
        except Exception as e:
            logger.error(f"Broadcast {method_name} failed: {e}")
        if self.hud_window:
            try:
                getattr(self.hud_window, method_name)(*args, **kwargs)
            except Exception as e:
                logger.error(f"HUD broadcast {method_name} failed: {e}")
\`\`\`

The key addition is the \`_on_event_execution_plan\` handler which was previously missing.
`.trim(),
    },
};

export const Empty: Story = {
    name: 'Empty Content',
    args: { content: '' },
};
