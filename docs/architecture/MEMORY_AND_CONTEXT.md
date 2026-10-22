# Chad's Memory & Context — How It Actually Works

Status: current implementation guide. Authority: `GraphRAGClient.initialize()`,
`_sync_seed_facts_from_markdown()` and the prompt/context code in this checkout.

Everything that reaches Chad before he responds, where it comes from,
how to change it, and how to debug it when something seems off.

---

## What Goes Into Every Response

Before Chad says a word, these things are assembled in order:

```
1. Persona / identity         — Who Chad is (tone, rules, formatting)
2. Current date               — Today, so date-math isn't guessed
3. Core profile               — Who the USER is. Always on, never retrieved.
4. Tool list                  — Relevant tools for this specific query
5. Graph context boost        — Personal life facts, query-matched
6. Personal context block     — Graph facts matched per LLM call
7. Conversation history       — This session's message thread
```

Items 5 and 6 are both sourced from the same knowledge graph.
They run at different points in the pipeline, explained below.

Item 3 is different in kind and that difference is the point — see below.

---

## The Core Profile (always-on identity)

**File:** `src/core/intelligence/prompt_builder.py` → `core_profile_block()`
**Source of truth:** `## _core_profile` section of `config/personal_knowledge.md`
**Stored as:** the singleton `core_profile:main` node in the graph
**Budget:** `knowledge_graph.core_profile_max_chars` (default 800)

Everything else personal reaches the model through query-matched retrieval —
similarity threshold, item cap, char budget. That is correct for the long tail
and wrong for the handful of facts that are load-bearing on almost any turn.

The July 2026 audit found this concretely: asked to plan a date night, Chad asked
the user what stage their relationship was in, while holding the engagement and
the wedding date in its graph. The relationship edge had been ranked out. Facts
you need on most turns must not be subject to ranking at all.

The core profile is injected verbatim, unconditionally, for user-facing scopes
(`supervisor`, `heartbeat`, `voice`). It lives in the **system prompt** rather
than the per-turn knowledge boost for the same reason as the current-date block:
it is stable across turns, so it costs one prefill and is KV-cached after that.

Editing it: change `## _core_profile` in `personal_knowledge.md` and restart —
the sync runs on every `initialize()`, no need to delete the graph. A profile
written by sleep-cognify (`source: sleep_cognify`, human-reviewed) is never
overwritten by the markdown.

Keep it short and boring: name, where you live, who you live with, what you do,
what's coming up. If Chad ever asks the user to re-explain something basic about
their own life, that fact belongs here.

### `## _behavior` — standing directives

Same mechanics, different job: how Chad should *act*, not who the user is. Injected
as `### USER PREFERENCES`, also always on, also never retrieved.

Directives previously lived in the graph as ordinary facts, where they were
semantically bland enough to weakly match almost anything — "prefer recent items
when using email/Drive memory" surfaced while the user was debugging a traceback.
Doctrine is not recall. If a line tells Chad what to *do* rather than what is
*true*, it belongs here.

---

## One fact per line, not one blob per section

Every non-reserved `## section` contributes **one fact node per line**. A whole
section used to be a single node, so retrieval returned it as a unit and then
compacted it to `max_item_chars` (120) — meaning the truncator picked which of
your facts survived. "What still needs booking for the wedding" got back the
wedding *date*; the dog's name was cut off the end of the family blob.

Write `personal_knowledge.md` as one fact per line and each stays intact, gets
scored on its own merits, and stops dragging its neighbours into the prompt.

Seed facts are **reconciled** with the markdown on every `initialize()`: lines you
add appear, lines you delete disappear, and edits take effect on restart — no more
deleting `knowledge_graph.json` to pick up a change. Only `source: seed` nodes are
touched (runtime saves are never reconciled away), and a fact you have `forget`-ed
stays invalid, matched by content, so re-seeding cannot resurrect it.

---

## The One Memory: The Knowledge Graph

**Chad has one personal memory.** Everything worth knowing about the user
lives in the knowledge graph.

| What | Where |
|---|---|
| Human-readable facts (source of truth) | `config/personal_knowledge.md` |
| Compiled graph (runtime) | `data/knowledge_graph.json` |
| Code that runs it | `src/core/intelligence/graph_rag_client.py` |

> The `user` persona fragment is **deprecated** (#589). It used to duplicate
> name/timezone/style here, went stale, and was eventually blanked by an
> unattended write without anyone noticing. It is now skipped at load time
> (`persona_store.DEPRECATED_IDS`). Edit `config/personal_knowledge.md` instead —
> that is the only place user facts belong.

### How it gets populated

1. **Seed facts** — On startup, `GraphRAGClient` reads
   `config/personal_knowledge.md`, seeds an empty graph, and reconciles seed
   facts on every initialization. Each non-reserved fact line becomes a node.

2. **Runtime saves** — Personal facts enter the graph via:
   - `manage_user_memory` with `save` → exact desktop approval → `GraphRAGClient.ingest()`
   - Reflective lessons (`user` scope) require review before application.
   - Legacy `[[learn: fact]]` directives are ignored: they have no approval channel.
   - Proactive extraction is not registered on turn completion. Its library pipeline
     returns quality-checked proposals and cannot ingest them directly.
   
   All paths persist immediately to `knowledge_graph.json`.

3. **Direct editing** — Open `config/personal_knowledge.md`, edit freely,
   restart Chad to reconcile seed facts. Do not delete the graph for routine
   edits: it also contains runtime facts and invalidation history.

### The write gate — harness structures, model decides

The approved `manage_user_memory` save goes through `MemoryWriteGate`
(`src/core/intelligence/memory_write_gate.py`) before reaching the graph. Quality
gating is separate from consent and cannot replace it. Reactive reflection now
creates reviewable proposals; `reflection.propose_on_failure` defaults off.
Ingestion daemons don't touch the graph at all.

```
normalize  strip "please remember that", collapse whitespace
atomize    one fact per line/sentence, so nothing gets truncated later
validate   reject tasks, questions, paragraphs, pronoun-leading fragments
dedup      skip if ≥0.80 similar to an existing fact
conflict   ≥0.55 but below duplicate → save, and surface the older fact
```

No LLM call anywhere in that pipeline.

**Why the model isn't asked for a schema.** Hard schema decoding costs small
models real accuracy — [arXiv:2605.26128](https://arxiv.org/abs/2605.26128)
measured a 43.5-point drop in executable accuracy on a tool-call analogue while
*schema validity stayed at 100%*: the syntax holds and the content quietly rots.
So the model supplies a plain sentence (the thing it is reliably good at) and
answers the judgment question — *is this worth remembering?* Everything
mechanical is the harness's job.

**Rejections are prompts.** Every skip returns a short reason that says what to do
instead ("starts with a pronoun, so it won't make sense on its own later — name
the person explicitly"). That is the adaptation channel: the model doesn't need to
be trained on our rules, it needs to recover in one retry. A save that stored
nothing still returns `success: true` — "already known" is a correct outcome, and
reporting it as failure made the model retry the same rejected text.

**Thresholds are measured, not guessed.** Restatements of a known fact scored
0.83–1.00; genuinely new facts topped out at 0.43. The duplicate threshold sits
near the duplicate floor rather than the midpoint on purpose: wrongly dropping a
new fact loses information permanently, wrongly keeping a near-duplicate costs one
node. Tune under `knowledge_graph.write` in `config.yaml`, and re-measure first.

**Two detection mechanisms, and they mean different things.** Embedding
similarity only sees a supersession when the sentence *shape* survives the change
("favorite foods are X" → "...are Y" scores 0.73). When the changed *value*
dominates — "now works at Redwing Marine" replacing a different employer — it
scores 0.43, inside the genuinely-new band, with the wrong nearest neighbour. No
threshold separates those, so that case is caught **structurally** instead
([#656](https://github.com/MatthewLoyed/AIAssistant/issues/656)): see
single-valued relations below. An embedding hit is a *guess* that leaves the old
fact live and asks the model to forget it; a structural hit is a *certainty* that
has already expired the old edge and tells the model there is nothing to do.

Check the gate with `python -m evals.memory_write_probe` (21 cases, zero tokens).

### Single-valued relations and temporal edges (#656)

Edges carry bi-temporal *valid time* (`valid_from` / `valid_to`) — when the
relation was true in the world, as opposed to when it was ingested. Following
Zep/Graphiti ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)): invalidate,
never delete.

`SINGLE_VALUED_RELATIONS` (`personal_knowledge_parse.py`) lists the relations
where the user can only have one live edge at a time — `employed_by`, `lives_in`,
`partner_of`. Asserting a new value stamps `valid_to` on the old edge instead of
adding a rival for retrieval to choose between.

The list is deliberately tiny, because the failure modes are asymmetric: keeping a
stale edge costs one competing walk line, while wrongly expiring a live one makes
a true fact vanish from retrieval. That is also why employment has its own
relation rather than overloading `member_of` — a second membership (a club, a
team) is perfectly normal and must not end someone's job.

Curated `## _graph` edges can author history directly:

```markdown
- Alex -employed_by-> Org:CurrentCo
- Alex -employed_by-> Org:OldCo (since 2021-04, until 2024-09)
```

An expired edge is excluded from every normal walk and surfaced — annotated
`until …` — only for an explicitly past-tense question ("where did I *used to*
work"). That takes both a past-tense cue and a personal-life subject, so "my
*previous* implementation" stays a coding question.

### How facts are invalidated

`forget()` marks nodes `valid: false` with a timestamp. They stop appearing
in search results but are never deleted from the JSON file. History is
always recoverable.

Since [#658](https://github.com/MatthewLoyed/AIAssistant/issues/658) it acts on
**one** node: candidates are scored by symmetric Jaccard overlap, and the best
match is invalidated only if it clears a minimum score *and* a clear margin over
the runner-up. Anything less decisive invalidates nothing and returns the
near-miss candidates for the model to disambiguate — a short description like
"my email" can no longer mass-invalidate. The tool result names exactly what was
forgotten.

---

## How It Reaches Chad — Two Injection Points

The graph is queried at **two different moments** per turn:

### Injection Point 1: Graph Context Boost (pre-routing)
**File:** `src/core/orchestration/nodes/graphrag_node.py` → `graphrag_injector_node`  
**When:** Before LangGraph routes the message — every non-chat, non-voice turn.  
**Trigger:** Unconditional. (There is no longer a `GRAPH_BOOST_KEYWORDS` gate;
older docs describing one are wrong.)  
**What it does:** Returns a `### RELATIONAL CONTEXT (GRAPH)` block in the
`knowledge_boost` state field, which the supervisor injects into the *trailing*
user turn — not the system prompt, so per-turn-volatile content doesn't force a
full KV-cache re-prefill every turn.

Retrieval is hybrid: typed-edge walk from entities named in the query, plus fact
similarity. Walk lines are ranked by query-intent match, then hop distance from
the seed, then the relation's intrinsic importance (`_RELATION_PRIORITY` —
`partner_of` and immediate family outrank `attended`/`friend_of`). The relation
name is only a final determinism tiebreak; it used to be the *effective* one,
which sorted `partner_of` last alphabetically.

### Injection Point 2: Personal Context Block (per LLM call)
**File:** `src/core/intelligence/memory_engine.py` → `get_unified_context()`  
**Called from:** `src/core/intelligence/prompt_builder.py` → `build()`  
**When:** Every time a system prompt is assembled for an LLM call.  
**What it does:** Queries the graph with the current user message, returns
up to 6 matching facts as a `### PERSONAL CONTEXT` bullet block.

Both points query the same graph. Point 1 is keyword-gated and
session-scoped. Point 2 runs on every call and is query-matched.

**Header:** injected blocks use `### PERSONAL CONTEXT` (not the legacy
`### RELEVANT RECALLED MEMORIES` label from older docs/prompts).

---

## Memory Policy (prompt behavior)

The `memory_policy` persona fragment (scope: `always`, `prompts/persona/memory_policy.md`
or your `config/persona/` override — see [PERSONA_PROMPTS.md](PERSONA_PROMPTS.md))
defines tiered consent for remembering facts:

| Tier | When | Action |
|---|---|---|
| 1 — Ask first | Sensitive (health, relationships, finances, credentials) | Ask before `save` |
| 2 — Offer | Durable preferences / life facts newly shared | Offer once, save on yes |
| 3 — Silent save | Low-risk operational mappings validated in-session | `save` + brief confirm |

When injected context contradicts the user, Chad should `forget` the stale fact
then save or ask per tier. Apply via `scripts/update_memory_persona.py`.

**Reflection (off hot path):** thumbs-down on a turn triggers a dry-run `propose_lessons`
pass (see `src/core/intelligence/reflection_trigger.py`). Proposals land in
`evals/runs/reflect_proposal_latest.md`; nothing auto-writes — run
`python -m src.core.intelligence.reflection --write` to apply admitted lessons.

---

## Memory vision (local-first)

Chad’s memory stack has three jobs. Keep them separate:

| Layer | Job | Store today |
|---|---|---|
| **Identity / life facts** | Who you are (family, goals, prefs) | `personal_knowledge.md` → NetworkX JSON graph |
| **Document location** | Where a file lives (Drive / filesystem) | Catalog rows in local sqlite |
| **Activity corpus** | What you saw/said recently (Gmail, Chrome, …) | `personal_index` in local sqlite |

**Storage rule (non-negotiable for OSS):** local is always primary. Supabase is an
**opt-in backup for personal identity only** — knowledge-graph facts + persona
prompt overlays when `knowledge_graph.storage: local_then_supabase`. Bulk Activity
(Chrome / Gmail / filesystem / Drive catalog) **never** leaves the machine.

| Knob | Local primary | Optional cloud mirror |
|---|---|---|
| `memory.storage` | sqlite Activity / memories | **no** (bulk stays on-disk; unset → `local`) |
| `drive_catalog.storage` | sqlite Drive catalog | **no** (`local_then_supabase` treated as local) |
| `knowledge_graph.storage` | md + `knowledge_graph.json` | yes (facts + persona overlay backup) |
| `langgraph.checkpointer_storage` | sqlite conversation checkpoints (`data/local_langgraph_checkpoints.db`) | **no** by default — opt-in Postgres-only escape hatch (no local copy; trades local-first for live inspection via Supabase/LangGraph Studio) |

**What GraphRAG is today:** fact nodes *plus* a typed entity/relation layer. A
query seeds entities, walks typed edges outward with ranking, and fills the
remaining budget with fact similarity. Edges carry `relation`, `confidence` and
bi-temporal validity, and single-valued relations supersede rather than accumulate
(#608–#611, then epic
[#655](https://github.com/MatthewLoyed/AIAssistant/issues/655): #656–#661).
Runtime saves join that typed layer too, so it grows from use instead of being
frozen at whatever `## _graph` was hand-written.

**Recent direction (this era):** catalog-first for “where?”, snippets/headers for
mail (body on-demand later), deepen personal corpus (sent mail, multi-year filters),
keep identity in editable local files.

---

## What Is NOT Personal Memory (graph)

These are separate from the knowledge graph. Some **are** auto-injected on user
turns (Activity + Drive catalog); they still must not be mixed into
`manage_user_memory` / graph identity facts.

| Store | What's in it | When it's used |
|---|---|---|
| `memories` / `personal_index` (local sqlite only) | Chrome, Gmail, Drive *activity* + local **filesystem catalog** (filename + path + mtime, issue #601) embeddings | Activity injector + `search_browser_history` |
| `memories` / `drive_catalog` (local sqlite only) | Drive **location** catalog (name + path + `file_id`) | `DOCUMENT CONTEXT (DRIVE)` injector; read body with `google_drive_read_file` |
| Legacy flat fact rows | Old personal fact rows | **Do not write** — dormant for context injection |
| `messages` / chat history (`local_chad.db` or Supabase) | Full GUI transcripts (every user/assistant message) | **Deliberate transcript search**: GUI `SearchModal` + `search_session_history` tool (local sqlite uses FTS5 + snippets; Supabase keeps `ilike`). Source of truth for exact phrases / URLs. |
| `session_log` | Consolidation summaries + indexed past-session exchanges (issue #614-session-memory), truncated checkpoint pairs | Auto-injected (`SESSION HISTORY` block, thresholded) + **semantic fallback** inside `search_session_history` when chat-history FTS returns no hits |
| LangGraph checkpoint | Conversation turns this session | Local sqlite by default (`langgraph.checkpointer_storage: local`) — same policy as the rest of the storage table below. `SessionHistoryIngestor` reads this file to populate `session_log`. |

**Transcript search roles (don't conflate):** chat-history FTS answers “find that YouTube link / exact phrase.” Vector `session_log` answers “what did we talk about regarding X?” when wording differs. The agent tool runs chat-history first, then falls back to `session_log` only when FTS returns no hits.
### Local-first storage

`memory.storage` / `drive_catalog.storage` (default story for OSS):

| Mode | Behavior |
|---|---|
| `local` | sqlite only (`data/local_chad.db`) — default; also the unset default |
| `local_then_supabase` | accepted for back-compat on memories/catalogs but **does not mirror** bulk rows |
| `supabase` | cloud-primary escape hatch (not recommended); Chrome/Gmail/filesystem/Drive catalog still forced onto sqlite |

Unset `memory.storage` → `local` (memories are not a Supabase backup target).

**Personal / prompts backup** uses `knowledge_graph.storage: local_then_supabase`:
mirror graph facts after save, and best-effort upsert persona overlays into
`persona_config` (files remain source of truth).

**Local filesystem catalog** (`FilesystemCatalogIngestor`, issue #601) writes through the
same `create_memory` path as Chrome/Gmail/Drive activity (category `personal_index`), so
it stays local-only with them — no separate `filesystem_catalog.storage` key. Config lives
under `filesystem_catalog:` in `config.yaml`: `roots` is an explicit allowlist, empty by
default (no scan even if the ingestion daemon is on — never index a whole drive by
accident). v1 is catalog-only (filename/path/mtime), no file-content reading.

**Ingest safety:** Activity rows are hash-deduped (append-only); Gmail upserts by
`message_id` (issue #617). Drive catalog upserts by `file_id`. Full-catalog reconcile
may drop rows for files gone from Drive, but refuses an empty keep-set or deleting more
than `drive_catalog.max_reconcile_delete_ratio` (default 25%) so a bad listing cannot
wipe a healthy catalog. Failed syncs do not advance watermarks past real progress.

### Drive catalog vs Activity Drive rows

| | Drive catalog | Drive activity ingest |
|---|---|---|
| Goal | “Where is that doc?” | “What changed recently?” |
| Content | Name + resolved path + id | Modified-file snapshots |
| Injector | `DOCUMENT CONTEXT (DRIVE)` | Activity block |
| Body | On-demand `google_drive_read_file` | Not full text |

One-shot: `scripts/maintenance/run_drive_catalog_sync.py` (needs Google token under `CHAD_HOME`).

### Gmail → Activity

Config caps (`memory.gmail_lookback_days`, `memory.gmail_max_results`) bound wall-clock;
`0` means uncapped (avoid for daytime). Prefer incremental watermark sync.
Cold lookback: `scripts/maintenance/run_gmail_ingest_sync.py --reset-watermark`
(does **not** delete existing rows — hash dedupe).

Embeddings for short catalog/email lines use in-process SentenceTransformers
(`embeddings.use_api: false` by default). LM Studio `/v1/embeddings` is optional
and was slower than CPU ST batch on AMD for these short strings.

---

## Debugging

### "Chad doesn't seem to know something it should"

1. Check `config/personal_knowledge.md` — is the fact in there?
2. If yes, check `data/knowledge_graph.json` — does the node exist and is `valid: true`?
3. Ask Chad: "what does your knowledge graph look like?" — it calls `get_status()`
   and reports topic/fact counts.
4. Ask Chad: "search your memory for X" — runs `manage_user_memory` with
   `search` action and returns what it finds.
5. If the fact is missing, say "remember that X" — Chad saves it to the graph.

### "Chad is injecting wrong/stale facts"

1. Open `data/knowledge_graph.json`, find the stale node, check its `valid` field.
2. Tell Chad: "forget that X" — runs `forget()` which marks it invalid.
3. Or edit `knowledge_graph.json` directly and set `"valid": false` on the node.

### "The graph isn't seeding"

1. Check that `config/personal_knowledge.md` exists and has `## sections`.
2. Delete `data/knowledge_graph.json` to force a re-seed on next startup.
3. Watch startup logs for `[ GRAPH ] ✓ Personal knowledge graph ready — N active facts`.
   If N is 0, the markdown file wasn't parsed correctly.

### "Chad retrieved the wrong facts / missed an obvious one"

Run `scripts/probe_memory_recall.py` — it prints the entity seeds, every walk
line in rank order, per-fact similarity scores against the relevance threshold,
and the final budgeted block, for any list of queries. No model calls, no server.
That turns "Chad felt dumb" into "this edge ranked 14th and only 3 fit."

If the fact should be known on *every* turn, the answer is usually the core
profile, not a better ranking.

To check nothing regressed across the board:

```bash
python -m evals.memory_recall_probe
```

37 scored cases (recall + negative controls) against a fictional persona fixture,
zero model tokens. Negative controls matter as much as recall: personal facts
leaking into coding questions is the same bug class as failing to recall them.

Those lanes all check the **harness**. To check whether the *model* actually saves
the right things — a question none of them can answer, since a model that never
calls `save` scores full marks on the write probe — run the live lane. It costs
model tokens, so it is deliberate and never in CI:

```bash
python -m evals.runner --memory-write-live
```

See `evals/README.md`. Current baseline and the open finding it produced:
[#674](https://github.com/MatthewLoyed/AIAssistant/issues/674).

### Tuning the relevance threshold

`knowledge_graph.relevance_threshold` was measured, not guessed — on the atomized
graph the worst true-positive top-1 score is ~0.28 and the best true-negative is
~0.23, so 0.25 sits between them. If you substantially change your fact set,
re-measure rather than nudging it: the probe prints the scores.

Two caveats worth knowing. Topic hints *multiply* a fact's similarity
(`_TOPIC_HINT_BOOST`); an earlier version floored them to a flat 0.70, which put a
whole topic above any threshold and destroyed its internal ranking whenever one
stray keyword matched ("how tcp handshakes **work**" pulled in every career fact).
And some collisions are genuine — "refactor the tool selector" really is close to
"internal tooling work" in embedding space. Past a point, tightening the ranker
just overfits; see the `note` fields in `evals/datasets/memory_recall.jsonl`.

### Useful log lines to watch

```
[ GRAPH ] ✓ Personal knowledge graph ready — N active facts, M relationships.
[ GRAPH ] Keyword match triggered. Querying local GraphRAG for: '...'
[ GRAPH ] Imbuing system prompt with relational context.
[ GRAPH ] Added node '...' (topic: ...).
```

Set `CHAD_LOG_LEVEL=DEBUG` for scored search output on every query.

---

## Files At a Glance

| File | Role |
|---|---|
| `config/personal_knowledge.md` | **Edit this** — human-readable source of truth for who the user is (incl. `## _core_profile`) |
| `scripts/probe_memory_recall.py` | Offline "why did/didn't this fact surface?" probe |
| `data/knowledge_graph.json` | Runtime graph — delete to re-seed, inspect to debug |
| `src/core/intelligence/graph_rag_client.py` | Owns the graph: load, seed, sync from markdown, write, forget, persist, migrate |
| `src/core/intelligence/graph_retrieval.py` | Query-time ranking/formatting (seeds, walk, budget) |
| `src/core/intelligence/graph_forget.py` | Pure scoring/decision logic for `forget()` |
| `src/core/intelligence/graph_runtime_relations.py` | Entity resolution + single-valued supersession for runtime saves |
| `src/core/intelligence/graph_always_on.py` | Sync, mtime-cached reads of the core-profile / behavior singletons |
| `src/core/intelligence/graph_backup.py` | Optional Supabase mirror of identity facts |
| `src/core/intelligence/memory_engine.py` | `get_unified_context()` — queries graph, formats context block |
| `src/core/orchestration/supervisor.py` | `graphrag_injector_node` + `GRAPH_BOOST_KEYWORDS` |
| `src/core/intelligence/prompt_builder.py` | Calls `get_unified_context()` per LLM call |
| `src/tools/system/utilities/manage_user_memory.py` | Chad's tool for save/search/forget/status |
| `prompts/persona/`, `config/persona/` | Persona fragments — see [PERSONA_PROMPTS.md](PERSONA_PROMPTS.md) |
| `scripts/update_memory_persona.py` | Apply memory_policy + agent MEMORY prompt updates |
| `config/config.yaml` → `knowledge_graph:` | Graph file path + max_results / walk / activity age settings |
| `config/config.yaml` → `memory:` / `drive_catalog:` / `embeddings:` | Local-first storage, Gmail caps, catalog sync, embed batching |
| `scripts/maintenance/run_drive_catalog_sync.py` | One-shot Drive location catalog |
| `scripts/maintenance/run_gmail_ingest_sync.py` | One-shot Gmail → Activity (optional `--reset-watermark`) |

---

## Research

How other agent-memory systems solve these problems, what we adopted, and the
measurements behind each threshold:
[AGENT_MEMORY_SYSTEMS_2026.md](../research/AGENT_MEMORY_SYSTEMS_2026.md).

## Research (typed relationships)

Typed entities/relations + wake-path walk injection are documented in
[TYPED_GRAPH_MEMORY.md](../research/TYPED_GRAPH_MEMORY.md) (#606, shipped). Overnight local-LLM
cognify (sleep path) remains #611.

## Task continuity after compaction

The existing [turn compactor](../../src/core/orchestration/turn/consolidation.py)
archives older messages, retains recent history and appends a wake summary.
Its optional `resume_context` preserves goal, constraints, next action, canonical
source/document references, decisions with reasons, rejected approaches and
verification state. Unknown fields and nested values are discarded; empty
fields are omitted. Old summaries remain readable without migration.

[Context formatting](../../src/core/utils/compaction_context.py) deduplicates
items and bounds the complete added block via
`consolidation.resume_context_max_chars` (default 2400 characters). The extractor
gets the advisory `resume_context_target_chars` (1800). The default cap is roughly
600 English tokens using Chad's character estimate; it is not an exact tokenizer
budget. A reference is kept whole or omitted, never truncated into an invalid
path. Limits are declared in the [limit registry](../../src/core/tools/limits.py).

This is task/session history, not another personal-memory store. Reopen referenced
sources; a summarized tool/document quote gains no instruction authority. Past
verification describes an earlier state and must be rerun after relevant changes.
The [retention tests](../../tests/unit/test_compaction_task_context.py) exercise
both summary paths, bounded repeated compaction, malformed input and old records;
[checkpoint tests](../../tests/unit/test_turn_consolidator_bounded_checkpoint.py)
cover the history reset separately. These fixture tests do not prove a live model
will extract every required fact; use the onboarding/resume eval for that.
