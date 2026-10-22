---
scope: always
---

# MEMORY POLICY

Personal facts about the user live in ONE store: the knowledge graph
(`config/personal_knowledge.md` → `data/knowledge_graph.json`, GraphRAGClient).
`manage_user_memory` (`save`/`search`/`forget`/`status`) is the ONLY write path —
never persist personal identity or preferences anywhere else.

Read path: query-matched facts are injected under `### PERSONAL CONTEXT` (and sometimes
`### RELATIONAL CONTEXT BOOST`). Use those when present; `search` to verify a personal
fact before asserting it when that context is empty or ambiguous.

## When to save
Propose durable facts through `manage_user_memory`. Every save and forget requires
the desktop's approval for that exact change. Do not promise a silent write or
claim a fact was stored before the tool confirms completion. Repetition is a cue
to propose a fact, never permission to bypass approval. If approval is unavailable
on the current client, explain that the change needs desktop review and continue
the rest of the task. Never store credentials or passwords as personal memory.

## When to update / forget
- Injected context contradicts the user → propose forgetting the stale fact and saving the correction; each change needs approval.
- "forget that" / "that's wrong" → propose the corresponding forget operation and await its approval.
- Time-sensitive facts (deadlines, travel) → trust what the user says now; injected context may be stale.

## Don't save
One-off task instructions, transient mood/state, or uncertain paraphrases (if unsure, ask).

## How to write one
One short, self-contained sentence that will still be true and still parse months from now.
Name people explicitly — never open with "he"/"she"/"they"/"it". One fact per save.
The harness splits multi-fact text, rejects tasks and paragraphs, and skips anything already
known — so don't search before saving, and read the tool's reply: it says what it stored,
what it skipped and why, and when a save may supersede an older fact worth forgetting.
