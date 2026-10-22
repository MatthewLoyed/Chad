# Typed Graph Memory — Research Note (#606)

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

**Status:** MVP (Slice A) ACCEPTED 2026-07-24 — see §7.4. Slice B (sleep cognify, #611) implemented same day — see §12.
**Date:** 2026-07-24  
**Code today:** `src/core/intelligence/graph_rag_client.py`  
**Related:** #601 (filesystem catalog), #448 (local-first), #99 / #238 (GraphRAG v1)

---

## 1. Problem

GraphRAG v1 stores personal knowledge as **fact blobs** (`##` sections → nodes) with soft `related` edges from keyword overlap. Retrieval is **embedding / keyword similarity only** — the graph is never walked.

That answers “what’s in my notes about family?” when the prose matches. It does **not** answer:

- `Jordan —partner_of→ Alex`
- `budget.xlsx —about→ Wedding2026 —in_service_of→ Goal:wedding_planning`
- “Why am I doing this?” / timelines across people, places, events, docs

Chad’s specialty is **24/7 free local LLM**. His weakness is **context limits and bloat**. The memory system must get smarter about relationships **without** feeding more prose into the prompt.

---

## 2. Survey (what the field is doing in 2026)

Sources: Synix eight-architecture analysis; Mem0 / Letta / Zep–Graphiti / Cognee comparisons; Hindsight observations + causal links; Letta sleep-time compute; local-first “no LLM on the read path” designs; GraphRAG token-optimization papers.

### 2.1 The axis that matters

Not “graph vs vector” — all serious systems are hybrid now. The split is **when you spend compute** and **how much structure you commit at write time**.

| System | Bet | Write | Read | Steal for Chad | Reject as product |
|---|---|---|---|---|---|
| **Mem0** | Schemaless facts; LLM extract per add | High (API $) | Cheap vector (+ entity boost) | Extract → reconcile loop | Weak multi-hop / “why” |
| **Letta** | Agent-edited memory **blocks** + sleep rethink | Async LLM | Tiny always-on blocks | Budgeted core profile + sleep agent | Full runtime swap |
| **Graphiti / Zep** | Bi-temporal typed KG | Very high (3+N LLM/episode) | No LLM: vector + BM25 + BFS | Temporal edges + hybrid walk | Neo4j / heavy ops |
| **Cognee** | Explicit cognify pipeline | High, batchable | Graph/vector modes | Explicit compile step + batch timing | Already rejected swap |
| **Hindsight** | Typed facts + **causal** links + background **observations** | Retain fast; consolidate async | RRF over semantic/BM25/graph/temporal | Causal edges + observation consolidation | External stack |
| **Local-first (e.g. PMB)** | SQLite + vectors; async embed | Cheap durable write | **No LLM on read** | Hard read-path rule | N/A |

**Cloud default:** starve writes (API cost).  
**Chad default:** spend freely on writes (local LLM); starve the **read/injection** budget (context is scarce).

### 2.2 Explicit non-adoptions

- Full Cognee / Graphiti / Mem0 / Letta / Neo4j
- LoRA / weight fine-tune as “sleep memory” (not auditable; fights model profiles)
- LLM on the hot-path retrieve or inject step
- Dumping more section prose into `### PERSONAL CONTEXT`

---

## 3. Recommendation — two-speed memory

One substrate: NetworkX + `data/knowledge_graph.json`, seeded from `config/personal_knowledge.md`.

```
Wake (every turn, no LLM)
  → always-on core block (tiny)
  → embed/keyword seed → walk typed+causal edges 1–2 hops
  → compact triple serialization (hard char budget)
  → ephemeral knowledge_boost (do not accumulate in chat history)

Sleep (nightly / idle, local LLM)
  → extract entities, relations, times, docs
  → add causal / in_service_of links
  → consolidate observations (evidence-linked)
  → invalidate superseded edges
  → rewrite core profile under char cap
  → Tier-1 sensitive → propose/approve (like reflection)
```

Human curated markdown remains SoT. Sleep writes an **inferred** layer. Filesystem/Drive catalogs (#601) stay external — graph stores **pointers only**.

### 3.1 Why this fits Chad

| Constraint | How the design answers |
|---|---|
| Free local LLM | Sleep cognify is write-heavy and offline |
| Context bloat | Compact triples + budgets + ephemeral inject |
| “Feels smarter about why” | `in_service_of` / `caused_by` + observations |
| Local-first | Files + NetworkX; no new graph DB |
| Existing patterns | Mirrors reflection propose→approve + cron |

---

## 4. Schema (MVP subset bold)

**Nodes**

- **`entity`:** Person | Project | Place | Document | Event | Org | Goal
- **`fact`:** raw prose episode (today’s nodes; provenance)
- `observation:` sleep-consolidated short belief + evidence fact ids (post-MVP / sleep slice)

**Edges**

- Social: **`partner_of`**, parent_of, child_of, sibling_of, friend_of, works_with
- Work: owns, member_of, **`employed_by`**, **`related_to_project`**
- Spatio-temporal: lives_in, located_at, attended, **`happened_at`**, **`occurs_on`**
- Docs: **`about`**, mentions, sourced_from
- Why: **`in_service_of`**, blocked_by, caused_by, motivated_by
- Soft: `related` (inferred, low confidence) — keep for migration

**Edge attrs:** `relation`, `valid_from` / `valid_to`, `confidence` (`curated` | `inferred` | `observed`), `source`, `created_at`. Nodes keep `valid: false` forget semantics.

**Single-valued relations (#656).** `employed_by`, `lives_in` and `partner_of` admit
one live edge per user, so asserting a new value stamps `valid_to` on the old edge
instead of adding a rival for retrieval to choose between. Everything else is
multi-valued and never auto-expires anything — which is exactly why employment gets
`employed_by` rather than overloading `member_of`, where a second membership (a club,
a team) is perfectly normal and must not expire the user's job. The canonical list
lives in `SINGLE_VALUED_RELATIONS` (`personal_knowledge_parse.py`); adding to it is a
semantics decision, since a wrongly expired edge makes a true fact vanish from the
walk while a wrongly kept one only costs a competing line.

### 4.1 Authoring (additive)

Plain `##` sections still work. Optional curated block:

```markdown
## family
Jordan is Alex's fiancée. ...

## _graph
- Person:Alex
- Person:Jordan
- Jordan -partner_of-> Alex
- Event:Wedding2026 -happened_at-> Place:Venue
- Document:budget.xlsx -about-> Event:Wedding2026
- Document:budget.xlsx -in_service_of-> Goal:wedding_planning
- Org:Acme (marine engineering, Portland)
- Alex -employed_by-> Org:Acme (since 2023-06, until 2025-03)
```

An **entity** may carry an optional attribute clause — free text rendered inline on
the walk line, so `Alex -attended-> Northgate` becomes
`Alex -attended-> Northgate (in Corvallis, Oregon)`. Without it an entity node
stores only its own name, and the line reaches the model carrying nothing it can act
on while still costing a slot in the identity budget (#659). Attributes on the ego
node are deliberately never rendered — the always-on core profile already covers the
user, so repeating it on every line would spend budget to say it a third time.

An **edge** may carry an optional validity clause — `(since X)`, `(until Y)`, or both.
`until` marks the edge as history: excluded from every normal walk, and surfaced
(annotated `until …`) only for an explicitly past-tense question like "where did I
*used to* work". Values are opaque strings; only emptiness is ever tested.

Deterministic parser for `_graph` — **no LLM required** for curated seed.

---

## 5. Injection budgets (efficiency is the feature)

| Layer | Content | Starting budget | Lifetime |
|---|---|---|---|
| Core profile | Identity + active goals + top relationships | ~400–800 chars | Stable / always-on |
| Turn subgraph | Seed entity + 1–2 hop paths | ≤ `max_results` lines / ~600–900 chars | Ephemeral `knowledge_boost` only |
| Raw fact prose | Fallback if no graph hit | 1–2 bullets | Same ephemeral |

Preferred serialization:

```text
Jordan -partner_of-> Alex
Wedding2026 -happened_at-> Venue; -occurs_on-> 2026-10-..
budget.xlsx -about-> Wedding2026; -in_service_of-> Goal:wedding_planning
```

Dedup turn hits already present in core. Do not append boosts into LangGraph chat history (already the `knowledge_boost` pattern).

---

## 6. Difficulty and MVP

### How hard is the full vision?

| Slice | Effort | Risk |
|---|---|---|
| **A — schema + walk + compact inject** | ~3–4 days | Low — extends existing client |
| **B — sleep cognify + observations + core rewrite** | ~2–4 days | Medium — extract quality, consent tiers |
| **Full (A+B + UI polish + evals)** | ~5–8 days | Medium |

### MVP (prove it helps memory) — Slice A only

**Goal:** With curated `_graph` triples in personal knowledge, Chad’s injected context for relationship queries is **path-aware and denser** than today’s fact-blob similarity — measurable in evals, visible in chat.

**In MVP**

1. Node `kind` + entity types; edge `relation` beyond `related`
2. Parse `## _graph` on seed / re-seed
3. `search`: embed/keyword seed → BFS 1–2 hops on typed edges → compact format
4. Hard char/line budget on injection
5. Migration: existing nodes = `kind=fact`; keep old soft edges
6. Eval probes (see §7)

**Out of MVP (Slice B+)**

- Overnight LLM extract
- Observations / causal auto-infer
- Core profile rewriter
- Memory UI graph explorer polish (list_relationships can show typed labels as stretch)

**Success criteria for MVP**

- Probe “who is in my family / who is Jordan” returns typed partner edge, not a lucky section blob
- Probe “what docs relate to Wedding2026” returns `Document -about-> Event` paths
- Injected boost for those queries stays under budget; no LLM calls on hot path
- Existing prose-only facts still retrieve via similarity (no regression)

---

## 7. Test plan (align before coding)

### 7.1 Unit / component

| Test | Asserts |
|---|---|
| `_graph` parser | Entities + typed edges from fixture markdown |
| Migration load | Old `knowledge_graph.json` loads; facts valid; soft edges preserved |
| Walk search | Seed on “Jordan” → includes `Jordan -partner_of-> Alex` |
| Budget | Formatter truncates to configured max chars/lines |
| Forget | Invalidating a fact/entity stops it appearing in walk results |

### 7.2 Eval harness probes (new cases under `evals/`)

| Id | Query | Expect |
|---|---|---|
| `mem_family_partner` | “Who is Jordan to me?” | Mentions fiancée/partner via graph path |
| `mem_event_doc` | “What documents relate to the wedding?” | Surfaces doc→event edge content |
| `mem_why_doc` | “Why do I have a wedding budget spreadsheet?” | Surfaces `in_service_of` / goal if curated |
| `mem_regression_identity` | “What’s my name / where am I based?” | Still answers from identity fact (embed path) |

### 7.3 Manual smoke

1. Add `_graph` to local `personal_knowledge.md`, delete `data/knowledge_graph.json`, restart
2. Ask family / wedding-doc questions; inspect injected `knowledge_boost` / PERSONAL CONTEXT
3. Confirm Settings → Memory still lists facts; relationships show typed labels if exposed

### 7.4 MVP A3 acceptance (#610) — PASSED 2026-07-24

Probes live in `evals/graph_memory_probe.py` + `evals/fixtures/graph_memory.jsonl`, run
via `python -m evals.runner --graph-memory` (free, no LLM — seeds a real `GraphRAGClient`
from a fixture `## _graph` block, exercising the production parse/seed/walk/budget path).

| Probe | Result |
|---|---|
| `mem_family_partner` — "Who is Jordan to me?" | PASS — typed `partner_of` edge surfaced |
| `mem_event_doc` — "What documents relate to the wedding?" | PASS — `Document -about-> Event` path |
| `mem_why_doc` — "Why do I have a wedding budget document?" | PASS — `in_service_of` → `Goal` edge |
| `mem_regression_identity` — "What is my location?" | PASS — still answered via the fact/embed path, no walk regression |

All 4/4 pass, injected boost stays under the configured char budget, zero LLM calls on
the retrieval path. `config/personal_knowledge.md` already carries a real `## _graph`
block (see the `## _graph` section — Person/Place/Org/Project/Event/Goal/Document
entities + curated edges), so no additional authoring was needed for MVP acceptance.
**MVP (Slice A) is DONE.** Sleep cognify (#611, Slice B) may proceed — see §12.

---

## 8. Implementation subtasks (tracking)

Parent: **#606**. Children (opened with this note):

| Issue | Slice | Delivers |
|---|---|---|
| **#608** | MVP A1 | Schema + `_graph` parse + migration |
| **#609** | MVP A2 | Seed→walk→compact search + budgets + unit tests |
| **#610** | MVP A3 | Eval probes + docs/config wiring; acceptance for MVP |
| **#611** | B (post-MVP) | Sleep cognify job (local LLM) + observations + core rewrite + propose queue |

Do not start B until A3 acceptance probes pass.

---

## 9. Migration notes

1. Load existing JSON nodes as `kind=fact`
2. Preserve `related` as `confidence=inferred`
3. On seed with `_graph`, upsert entities/edges by stable id (`Person:Jordan`)
4. Curated edges win over inferred on conflict
5. Re-seed path unchanged: edit markdown, delete graph JSON, restart

---

## 10. Non-goals (unchanged)

- Replace Drive/filesystem catalogs
- Supabase as primary personal memory
- Product swap to Cognee / Graphiti / Mem0 / Letta
- Bi-temporal full Graphiti engine (light validity fields only)

---

## 11. Next steps

1. ~~Document recommendation~~ (this note)
2. Open child issues #608–#611
3. ~~Implement MVP in order A1 (#608) → A2 (#609) → A3 (#610)~~ DONE — §7.4
4. ~~Only then schedule sleep cognify (#611)~~ DONE — §12

---

## 12. Slice B — sleep cognify implementation (#611) — 2026-07-24

Offline-only overnight pass, wired identically to the shape in §3/§6: extraction stays
write-side (local LLM, free, batchable); nothing here touches the hot read path.

**Delivered:**

- `src/core/intelligence/sleep_cognify.py` — mirrors `reflection.py`'s
  propose → gate → apply shape, applied to the graph instead of persona lessons:
  - `cognify(model)` — judge extracts typed edges (biased toward `in_service_of` /
    `caused_by` / `motivated_by` / `happened_at`), consolidated **observations** (with
    `evidence` fact ids), and an optional core-profile candidate, from the graph's
    valid fact + observation node corpus. Read-only.
  - `classify_tier(text)` — **deterministic**, no LLM — routes health / financial /
    legal / relationship-conflict content to Tier-1, same style as
    `graph_rag_client._detect_topic`.
  - `propose_cognify(model)` — gates: non-sensitive edges/observations above the
    confidence floor → `auto_admit_*`; anything Tier-1, or the core-profile candidate
    (always, regardless of tier) → `pending_review`. Writes nothing.
  - `apply_admitted(proposal)` — writes ONLY the auto-admit batch.
  - `apply_item(item)` / `persist_pending_items(proposal)` — Tier-1 + core-profile
    items are surfaced as `reflection_lessons` rows with `kind='sleep_cognify'`, reusing
    the existing Reflective Lessons review/approve queue and GUI panel (#194) rather than
    building a second one; `reflection_lessons_store.apply_lesson()` dispatches
    `kind='sleep_cognify'` rows to `sleep_cognify.apply_item()`.
  - CLI: dry-run by default; `--write` applies the auto-admit batch and still only
    queues Tier-1/core-profile items (never auto-writes them).
- `GraphRAGClient` additions: `add_observation()` (kind=`observation`, carries
  `evidence` fact ids, participates in retrieval/Memory-UI listing exactly like a fact
  node), `upsert_inferred_edge()` (auto-creates missing entity endpoints as `inferred`;
  a `curated` edge at the same key always wins, never overwritten), `get_core_profile()`
  / `set_core_profile()` (singleton `core_profile:main` node, hard char-capped, cheap
  dict-lookup read — no walk, no embedding).
- `heartbeat_service._maybe_run_sleep_cognify()` — own interval
  (`sleep_cognify.interval_hours`) AND an overnight local-hour gate
  (`sleep_cognify.night_hours_only` / `night_start_hour` / `night_end_hour`, default
  1am–5am) so it actually runs while the user is asleep, not mid-conversation. Disabled
  by default (`sleep_cognify.enabled: false` in `config/config.yaml`) — opt-in.
- Tests: `tests/unit/test_sleep_cognify.py` (tier classifier, propose/apply gating,
  `GraphRAGClient` additions, review-queue dispatch).

**Deliberately NOT wired into the wake path yet:** the always-on "core profile" block
from §3/§5 (`graphrag_injector_node` prepending `get_core_profile()` to every non-chat
turn) is scaffolded (`GraphRAGClient.get_core_profile()` is a cheap no-LLM read, ready
to call) but not yet injected in `src/core/orchestration/nodes/graphrag_node.py` — left
for a follow-up once a real approved core profile exists to verify against, to avoid
touching the hot path speculatively.

**Non-goals held (unchanged from §10):** no LoRA/weight fine-tune; no cognify on every
`manage_user_memory` save (still propose-only, off the interactive path); no Neo4j /
product swap.

---

## References (external)

- Synix — *Agent Memory Systems: A Source-Level Analysis of Eight Architectures* (2026)
- Dreaming.press — Cognee vs Graphiti vs Mem0 write-time schema axis
- Letta — Sleep-time compute / memory blocks
- Hindsight — Observations, causal links, background consolidation
- HackerNoon — Local-first memory: no LLM on the read path
- GraphRAG context optimization — compact / relation-grouped triples vs prose
