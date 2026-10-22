# Personal Knowledge Graph Setup

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

Chad's durable memory of **who you are** — identity, goals, health context, projects,
routine — lives in a local knowledge graph. Unlike browser Activity history (see
[BROWSER_HISTORY.md](BROWSER_HISTORY.md)), these are curated facts Chad injects into
every relevant conversation.

**Zero cloud required.** The graph is built and queried entirely on your machine.

---

## The two files that matter

| File | Role | Git status |
|---|---|---|
| `config/personal_knowledge.md` | **You edit this** — plain-English facts, one `## section` per topic | Gitignored (your data) |
| `data/knowledge_graph.json` | Runtime graph auto-built from the markdown file | Gitignored (generated) |

Template: `config/personal_knowledge.md.example` (tracked, generic placeholders).

---

## First-time setup (newcomer path)

### 1. Copy the template

`bootstrap_config.ps1` does this automatically on a fresh clone:

```powershell
.\scripts\setup\bootstrap_config.ps1
```

Or manually:

```powershell
copy config\personal_knowledge.md.example config\personal_knowledge.md
```

### 2. Fill in your real information

Open `config/personal_knowledge.md` and replace the placeholders (`[Your name]`,
`[Describe your family…]`, etc.). Each `## heading` becomes one fact node:

```markdown
## identity

Alex Example is a software engineer based in Toronto. Chad should use they/them pronouns.

## projects

Building a local-first AI assistant (this repo). Primary stack: Python, LangGraph, React.
```

You do **not** need to format it specially — plain English is fine.

### Optional: typed relationships (`## _graph`)

For people / places / events / docs Chad should walk as a graph, add a
`## _graph` section (see [TYPED_GRAPH_MEMORY.md](../research/TYPED_GRAPH_MEMORY.md)):

```markdown
## _graph
- Person:Alex
- Person:Sam
- Sam -partner_of-> Alex
```

Delete `data/knowledge_graph.json` and restart to re-seed after editing `_graph`.
Typed edges are walked at query time (#609): e.g. asking about Alex can inject
`Jordan -partner_of-> Alex` when that edge is curated.

### 3. Restart Chad (or start fresh)

On Brain startup, `GraphRAGClient` reads `personal_knowledge.md` and seeds
`data/knowledge_graph.json` if the graph is empty.

Watch for this log line:

```
[ GRAPH ] ✓ Personal knowledge graph ready — N active facts, M relationships.
```

### 4. Verify in the GUI

Settings → Memory → **User Facts** — seeded topics from your markdown sections.

In chat: *"What do you know about me?"* — Chad calls `manage_user_memory` with
`status` or `search`.

---

## Runtime updates (no restart needed)

Chad can learn new facts during conversation:

| Method | Example |
|---|---|
| Ask Chad to remember | *"Remember that I'm allergic to shellfish"* |
| `[[learn: fact]]` directive | Internal — Chad saves after you confirm |
| `manage_user_memory` tool | Chad uses `save` / `forget` / `search` actions |

All runtime saves persist immediately to `data/knowledge_graph.json`.

To **reset** from your markdown file: delete `data/knowledge_graph.json` and restart Chad.

---

## How it reaches Chad

Two injection points per turn (see [MEMORY_AND_CONTEXT.md](../architecture/MEMORY_AND_CONTEXT.md)):

1. **Relational context boost** — keyword-triggered block in the supervisor chain
2. **Personal context block** — query-matched facts on every LLM call

Both read the same graph. Activity/browser history is a **separate** store and does not
pollute these identity facts.

**Limits today:** the graph is fact-blob retrieval (embedding/keyword), not typed
relationship traversal. Soft edges exist; “who is connected to X?” is weak. See
research issue [#606](https://github.com/MatthewLoyed/AIAssistant/issues/606) —
do not invent a second memory product until that research lands.

---

## Configuration

In `config/config.yaml`:

```yaml
knowledge_graph:
  enabled: true
  storage: local   # or local_then_supabase to mirror facts when Supabase is enabled
  graph_path: data/knowledge_graph.json
  max_results: 4
```

**Local-first:** `personal_knowledge.md` + `knowledge_graph.json` are always the source of
truth (resolved via `CHAD_HOME` when set). `storage: local_then_supabase` best-effort
copies facts to Supabase after save — cloud is a mirror, never required to boot.

If you previously had Supabase flat-facts and a local `data/memories_flatfact_backup_*.json`,
rebuild the markdown (gitignored) with:

```powershell
$env:CHAD_HOME="<main checkout>"
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe scripts\maintenance\restore_personal_knowledge_from_flatfact.py
```

Set `enabled: false` to disable graph injection entirely (Chad still boots).

---

## Privacy

- `personal_knowledge.md` and `knowledge_graph.json` are **gitignored** — never commit them.
- The tracked `.example` file uses fictional placeholders only — that is what new OSS clones get.
- See [PRIVATE_DATA.md](../guides/PRIVATE_DATA.md) for the full private-data map.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Chad doesn't know basic facts about me | Check `personal_knowledge.md` exists and has content under `##` sections |
| Graph shows 0 facts on startup | Delete `data/knowledge_graph.json`; confirm markdown has non-empty sections; restart |
| Stale/wrong fact | Tell Chad *"forget that X"* or edit the markdown and delete the graph file |
| User Facts empty in Settings | Graph still initializing — wait a few seconds and refresh |

---

## Related docs

- [COLD_START.md](COLD_START.md) — full clone-to-running path
- [PRIVATE_DATA.md](../guides/PRIVATE_DATA.md) — secrets vs knowledge vs activity
- [MEMORY_AND_CONTEXT.md](../architecture/MEMORY_AND_CONTEXT.md) — deep architecture + debugging
- [TYPED_GRAPH_MEMORY.md](../research/TYPED_GRAPH_MEMORY.md) — research: typed relationships, walk retrieval, sleep cognify (#606)
