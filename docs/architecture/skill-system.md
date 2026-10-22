# Chad Skill System Architecture

> **Canonical reference for the three-primitive taxonomy and progressive disclosure pattern.**

---

## The Three Primitives

Chad's intelligence stack is built on three distinct primitives. Each has a clear cost model and use case:

| Primitive | Symbol | What it is | Cost | State | Defined in |
|-----------|--------|-----------|------|-------|-----------|
| **Skill** | 📖 | Packaged expertise markdown. Injects domain knowledge on demand. No code runs. | ~50–2000 tokens | Stateless | `src/skills/**/SKILL.md` |
| **Tool Call** | 🔧 | Discrete action executor. Input → Output. Deterministic. | Minimal | Stateless | `src/tools/**/*.py` |
| **Subagent** | 🤖 | Delegated collaborator with its own ReAct loop, tools, and context. | High | Stateful | `src/skills/**/agent.py`, `src/core/orchestration/agents/` |

---

## Decision Tree

```
1. Does the agent need domain-specific HOW-TO guidance?
   YES → Load a Skill (SKILL.md)
   NO  → next question

2. Is the task a single, discrete action?
   YES → Chain Tool Calls
   NO  → next question

3. Does the task need multi-step reasoning, its own history,
   or can it run in parallel with other tasks?
   YES → Use a Subagent
   NO  → probably just chain tool calls
```

---

## Progressive Disclosure (How Skills Load)

```
Step 1 — Discovery (~50 tokens, ALWAYS loaded)
  Supervisor sees: name + description in system prompt skill catalog
  e.g. "- morning_briefing [calendar, email]: Generates a prioritized briefing..."

Step 2 — Activation (~500–2000 tokens, ON DEMAND)
  User: "Give me a morning briefing"
  → description matches
  → Supervisor calls: load_skill("morning_briefing")
  → Full SKILL.md content returned as ToolMessage

Step 3 — Execution
  Supervisor reads SOP from SKILL.md content
  Follows the instructions directly, OR
  Delegates to the linked subagent (specified in SKILL.md frontmatter)
```

---

## File Structure

```
src/skills/
├── morning_briefing/
│   ├── SKILL.md          📖 Skill primitive (lightweight, always available)
│   └── agent.py          🤖 Subagent (for complex stateful work)
├── homework_assistant/
│   ├── SKILL.md
│   └── agent.py
└── organize_folder/
    ├── SKILL.md
    └── agent.py
```

---

## SKILL.md Format

Every `SKILL.md` must have YAML frontmatter:

```yaml
---
name: skill-name           # unique identifier; slash aliases accept hyphens/underscores
description: "..."         # ~1-2 sentences, used in system prompt catalog
version: 1.0.0
tags: [tag1, tag2]         # for filtering via list_skills(tag_filter=...)
subagent: agent_class_name # optional: links to the Python subagent
enabled: true
---
```

Followed by markdown content:
- `## What This Skill Does` — overview
- `## Tools Required` — tool dependencies
- `## Standard Operating Procedure (SOP)` — step-by-step instructions
- `## Critical Rules` — guardrails
- `## Subagent Delegation` — when to delegate to the Python subagent

---

## Catalog and Source of Truth

Local `src/skills/**/SKILL.md` files are the source of truth. The loader scans
allowlisted, enabled skills at startup and caches their metadata locally. Supabase
is an optional backup when the local catalog is empty; it is not required to
register a local skill. The full guide is read from disk when activated.

A new built-in skill needs its local guide and an entry in
`src/core/skill_system/skill_allowlist.py`. Restart the Brain through the normal
release flow to discover newly installed skills; do not restart production during
a coding task.

## User invocation and reusable work

Users can discover skills by typing `/`, open the catalog with `/skills`, and
invoke a named skill with inputs such as `/microsoft-assignment 5`. The GUI's
**Use in chat** action inserts the command for review before sending. The Brain
resolves the guide and passes its arguments to the supervisor's tool-capable
agent lane. This is explicit activation through the same active-skill state used
by progressive disclosure.

- [Slash skills, argument handling, and private resource configuration](../features/SKILLS.md)
- [Fixed-call routines and their execution limits](../features/ROUTINES.md)

---

## Supervisor Integration

The supervisor gets the skill catalog **always injected** into its system prompt at the bottom of the orchestration logic block. This costs ~50 tokens per skill (trivial for 3–10 skills).

```
### 📖 AVAILABLE SKILLS (call `load_skill` to activate any)
- **morning_briefing** [calendar, email, tasks]: Generates a prioritized morning briefing...
- **homework_assistant** [writing, canvas]: Helps draft academic assignments...
```

When a skill is activated via `load_skill()`, its content is stored in `ChadState.loaded_skill` and the supervisor gets a `🟢 ACTIVE SKILL` stub:

- **No `subagent` in frontmatter** (canva, kroki, presenton, typst, excalidraw, …): supervisor follows the SOP itself with bound tools / `call_mcp_tool`.
- **`subagent: …` set** (homework_assistant, frontend-design → code_agent, …): stub tells the supervisor to `delegate_to_<agent>` — do not default ownerless skills to code_agent.

---

## Adding a New Skill

1. **Create Directory**: `src/skills/<skill_name>/`
2. **Create Content**: Add `SKILL.md` with valid frontmatter.
3. **Allowlist**: Add the skill name to `src/core/skill_system/skill_allowlist.py` (required for `load_skill()`).
4. **Add Subagent (Optional)**: Add `agent.py` if complex execution is needed.
5. **MCP connectors**: If the skill teaches a Connectors MCP (e.g. Canva, Excalidraw), also add the catalog tile / `SHIPPED_MCP_SERVER_IDS` entry and a row in visual routing when it is a visual capability.
6. **Optional cloud index**: Run `python scripts/maintenance/sync_skills_to_supabase.py` when using the Supabase skills catalog.
7. **Deploy**: Ensure the local file is committed/deployed to the same relative path on the target server.

---

## Reference Repositories

The following repos were studied as reference implementations:

| Repo | URL | Key Insight |
|------|-----|-------------|
| `pessini/langgraph-skills-agent` | https://github.com/pessini/langgraph-skills-agent | Progressive disclosure pattern, SKILL.md frontmatter format |
| `SpillwaveSolutions/mastering-langgraph-agent-skill` | https://github.com/SpillwaveSolutions/mastering-langgraph-agent-skill | LangGraph SKILL.md for AI coding agents (used as reference for our own skills) |
| `langchain-ai/langchain-skills` | https://github.com/langchain-ai/langchain-skills | Installed globally via `npx skills add` — provides 11 skills for Antigravity |

---

## Installed Antigravity Skills (langchain-ai/langchain-skills)

Installed globally via: `npx skills add langchain-ai/langchain-skills --skill '*' --yes --global`

| Skill | Purpose |
|-------|---------|
| `deep-agents-core` | Core deep agent patterns |
| `deep-agents-memory` | Memory patterns for agents |
| `deep-agents-orchestration` | Multi-agent orchestration |
| `framework-selection` | Choosing the right LangChain framework |
| `langchain-dependencies` | Dependency management |
| `langchain-fundamentals` | LangChain core patterns |
| `langchain-middleware` | Middleware and callbacks |
| `langchain-rag` | RAG patterns |
| `langgraph-fundamentals` | LangGraph state machines |
| `langgraph-human-in-the-loop` | Interrupt and approval patterns |
| `langgraph-persistence` | Checkpointers and memory |
