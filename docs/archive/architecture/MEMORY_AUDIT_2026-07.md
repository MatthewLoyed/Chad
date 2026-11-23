# Memory Audit — July 2026

> Historical evidence/research. For current behavior use [MEMORY_AND_CONTEXT.md](MEMORY_AND_CONTEXT.md).
> Results and proposals below describe their original measurement period.

> **Status:** P0–P4 are implemented and verified. Measured end state on the real
> graph: **36/36 recall, 4/4 negative controls** (from 34/36 and 1/4). The tracked
> CI eval (`python -m evals.memory_recall_probe`, fictional fixture) was 31/31 at
> audit time and is **37/37** today.
> Findings below are kept as written at audit time — see
> "What shipped after this audit (epic #655)" at the end for what has since
> changed, including the 0.43 supersession blind spot, which is now closed.

Audit of what Chad actually recalls, what reaches the model each turn, what is
missing, and what is wasted. Triggered by a reproducible recall failure on a
personalization request (see "The failure" below).

Examples in this document use anonymized placeholders (`Person:Partner`) for the
real graph contents. Reproduce locally with `scripts/probe_memory_recall.py`,
which prints entity seeds, walk lines, scored fact hits, and the final boost
block for a list of queries — no model calls, no server.

---

## The failure

The user asked, in one message, for a fully personalized date plan — venues,
prices, dessert, outfits, physical affection, three options. Chad replied with a
clarifying question about **what stage the relationship was in**, despite the
graph containing the partner's name, the engagement, and the wedding date.

This is not a model failure. It is a retrieval-ranking failure, and it
reproduces deterministically offline.

### Reproduction

`scripts/probe_memory_recall.py` on the live graph (52 nodes, 49 typed edges):

| Query | Boost block, line 1–3 | Partner edge present? |
|---|---|---|
| `plan me a date with <partner name>` | `attended -> BS_Graduation`, `attended -> MS_Graduation`, `best_man_of` | **no** |
| `plan a date night with my fiancee` | **`Partner -partner_of-> User`**, `about -> <project>`, `attended` | yes, rank 1 |

Same graph. Same partner. The retrieval succeeds only when the user says the
*role word* Chad already has a keyword rule for, and fails when they say the
partner's *actual name*.

---

## Root cause — four compounding bugs

All four live in `src/core/intelligence/graph_rag_client.py`.

### 1. Alphabetical tiebreak buries the most important edge

`edge_rank()` (`_collect_walk_lines`) returns `(2, 0, relation)` for any edge
whose relation isn't boosted by a query keyword. `scored_lines.sort(key=item[0])`
then orders that entire bucket **by relation name**:

```
attended < best_man_of < friend_of < groomsman_of < ... < partner_of
```

`partner_of` is near the end of the alphabet, and only
`walk_cap = max(2, max_results // 2) = 3` walk lines reach the block. The single
most important edge in a personal knowledge graph loses to a string comparison.

### 2. Hop distance from the seed is ignored

The seed for the failing query was `Person:Partner`. Every line that won is an
edge incident to `Person:User` — two hops away. Nothing in the ranking prefers
edges touching the seed over edges found later in the BFS.

### 3. `relation_boosts` has no vocabulary for "do something with a person"

The relation-boost table maps `fiancé / partner / girlfriend / wife / …` →
`partner_of`. It has no entry for **date, gift, birthday, anniversary, dinner,
surprise, plan, together, for her, for him**. The entire class of "help me do
something for the person I love" queries falls through to the unranked bucket.

### 4. Multi-fact nodes lose the fact you needed to truncation

The `family` node is a single 568-char blob covering father, mother, brother,
sister, partner, in-laws, and a dog. `knowledge_graph.max_item_chars: 120`
compacts it query-relevantly. On a partner-gift query the compaction selected the
in-laws-and-dog sentence and **dropped the engagement fact entirely**.

Monolithic nodes plus a 120-char window makes the surfacing of any individual
fact a coin flip, even when the right node is retrieved.

---

## The structural gap — nothing is unconditional

There is **no always-resident identity block**. "The user is engaged; wedding is
May 2027" is subject to embedding similarity, a `0.22` threshold, a 6-item cap,
and an 800-char budget, on every single turn. If any of those go the wrong way,
Chad does not know who the user is married to.

Every leading memory system does the opposite for exactly this class of fact:

| System | Always-in-context | Retrieved on demand |
|---|---|---|
| **Letta / MemGPT** | core memory blocks (persona, human), size-limited, embedded in system prompt | archival memory via `archival_memory_search` |
| **Zep / Graphiti** | recent + salient subgraph summary | temporal KG search, bi-temporal edge invalidation |
| **Mem0** | user-scoped fact set | vector search over atomic extracted facts |
| **Chad today** | *(nothing)* | everything |

### `get_core_profile()` was built and never wired up

`graph_rag_client.py:447`:

```python
async def get_core_profile(self) -> Optional[str]:
    """Cheap always-on read — no LLM, no walk. Returns None if never written."""
```

The only callers in the repo are in `sleep_cognify.py` (the writer). Nothing in
`prompt_builder.py`, `graphrag_node.py`, or `supervisor_node.py` reads it. The
always-on core memory block exists, is hard-capped at 800 chars, and **never
reaches the model**. There is also no `core_profile:main` node in the live graph,
so sleep-cognify has never applied one either.

This is the single highest-leverage fix in the audit.

---

## What is "too much"

### Identity is out-competed 11:6 by activity noise

Per turn the injector can emit:

| Block | Items |
|---|---|
| `RELATIONAL CONTEXT (GRAPH)` — who you are | 6 (≈3 spent on walk triples) |
| `ACTIVITY CONTEXT (PERSONAL INDEX)` — browsing/mail | 3 |
| `DOCUMENT CONTEXT (DRIVE)` | 5 |
| `SESSION HISTORY` | 3 |

Who-you-are gets 6 of 17 bullets, and roughly half of those go to walk triples.

### Walk triples carry almost no information

`User -attended-> BS_Graduation` has no date, no degree, no institution — those
attributes were never stored, because the entity node's `content` is just its own
name (`"BS_Graduation"`). Three of six identity slots routinely go to strings the
model cannot act on.

### Behavior config is stored as an identity fact

The `identity` node mixes real identity (name, location) with physical stats
(height, weight) *and* a behavior directive: "The user prefers that clarifying
questions be presented using the `ask_user_question` tool." That directive scored
`0.321` on the failing date query — it competes for retrieval slots against the
engagement fact, and it belongs in a persona fragment, not in personal memory.

Note the irony: the failure mode was an unnecessary clarifying question, and the
node that survived retrieval was the one *about* clarifying questions.

### Score saturation flattens ranking

`_score_nodes` applies a topic-hint as `score = max(score, 0.55) + 0.15` → a flat
`0.70`. On one probe query two different nodes both scored **exactly 0.700**.
Once nodes tie at the cap, their relative order is arbitrary.

---

## What we do well

Worth protecting through any refactor:

- **Local-first with one write path.** `manage_user_memory` → `GraphRAGClient` is
  the only way personal facts enter. No competing stores.
- **Non-destructive forget.** `valid: false` + timestamp; history always
  recoverable.
- **A real typed layer already exists.** 49 curated edges with `relation`,
  `confidence`, `valid_from`, `valid_to`. The substrate for the fix is built —
  only the ranking on top of it is broken.
- **KV-cache-aware injection.** `graphrag_node` deliberately returns the boost in
  a separate field so the supervisor can put it in the *trailing* turn rather
  than the system prompt, avoiding a full re-prefill every turn. This is a
  sophisticated call most systems get wrong.
- **Parallel fan-out with failure isolation.** Four knowledge clients queried
  concurrently; a client exception is logged and skipped, never raised.
- **Anaphora handling.** `build_knowledge_query` blends prior user turns when the
  current message is short or pronoun-heavy, with no LLM call.
- **Consent tiering.** `memory_policy.md`'s three tiers are a genuinely good
  policy for a personal assistant.
- **"Speak as if you already know."** `memory_application.md` correctly forbids
  narrating retrieval — the right instinct for the "just knows everything" feel.
- **Everything is bounded.** Many systems dump the whole store; Chad has explicit
  per-source item and char budgets.

---

## Documentation and prompt drift

- `memory_application.md` tells the model its sources are `### PERSONAL CONTEXT`,
  `### RELATIONAL CONTEXT BOOST`, `### VOICE CONTEXT`. The LangGraph path
  actually emits `### RELATIONAL CONTEXT (GRAPH)`,
  `### ACTIVITY CONTEXT (PERSONAL INDEX)`, `### DOCUMENT CONTEXT (DRIVE)`, and
  `### SESSION HISTORY`. Two of the three headers the persona names never appear;
  three of the four that do appear are never named.
- `MEMORY_AND_CONTEXT.md` documents a keyword-gated injector driven by
  `GRAPH_BOOST_KEYWORDS` in `supervisor.py`. That symbol no longer exists —
  injection is now unconditional on non-chat turns.
- **Worktree graphs seed from the template.** A worktree's
  `data/knowledge_graph.json` is seeded from `personal_knowledge.md.example`, so
  every node is `[Your name] is the user`-style placeholder text. Any eval run
  from a worktree silently tests recall against a fictional person.

---

## Recommendations

### P0 — ranking fixes (small, surgical, high payoff) — **DONE**

1. **Rank walk edges by hop distance from the seed first.** Carry `depth` into
   `scored_lines` and sort on it before the boost tuple. Kills the alphabetical
   tiebreak by making it unreachable for seed-adjacent edges.
2. **Give core relations an intrinsic priority floor**, independent of query
   keywords. A static `RELATION_PRIORITY` map where `partner_of`, `parent_of`,
   `child_of`, `sibling_of` outrank `attended` / `friend_of` / `member_of`
   unconditionally.
3. **Always include the ego node as a seed.** Today `Person:User` seeds only via
   a hardcoded phrase list (`"my fiancée"`, `"my job"`, …). Naming a person seeds
   only that person, so the relationship connecting them to the user is never
   preferred.
4. **Extend `relation_boosts` with intent vocabulary**: date, gift, birthday,
   anniversary, dinner, surprise, plan, together → `partner_of`.

### P1 — the real fix: wire up the always-on core profile — **DONE**

5. **Read `get_core_profile()` on the wake path.** Inject it unconditionally,
   above the query-matched block, on every non-chat turn, with its own budget and
   no similarity threshold. Because it is stable across turns it belongs in the
   *system prompt* (unlike the volatile boost) — so it is KV-cached and costs
   nothing after the first turn.
6. **Seed it by hand now.** Do not wait on sleep-cognify, which has never
   produced one. Write the ~10 facts relevant to a plurality of turns: name,
   location, partner + wedding date, job, school status, active projects, the
   shape of a normal day. Not height and weight.

This alone would have prevented the reported failure and essentially every
failure of its shape.

### P2 — atomize facts — **DONE**

7. **One fact per node.** Split the 568-char `family` blob into
   `Fiancée is <name>`, `Wedding is <date> at <venue>`, etc. This fixes the
   truncation lottery *and* the score saturation, and makes `max_item_chars: 120`
   non-lossy instead of destructive. `personal_knowledge.md` becomes
   bullet-per-fact rather than section-per-topic. This is what Mem0 and Zep both
   do at ingest, and it is why their retrieval is precise.
8. **Store attributes on entity nodes** so a walk line can render
   `User -attended-> BS Graduation (MSU, May 2026)` instead of a bare id.

### P3 — hygiene — **DONE**

9. **Move behavior directives out of fact nodes** into persona fragments
   (starting with the `ask_user_question` preference in the `identity` node).
10. **Fix score saturation** — make the topic-hint multiplicative
    (`score * 1.3`) or an additive bonus on the true similarity, rather than a
    floor that flattens distinct nodes to an identical `0.70`.
11. **Rebalance budgets** so identity is not outnumbered by activity + drive +
    session. Either raise the graph cap with a reserved allocation for prose
    facts, or lower the Drive catalog's 5.
12. **Reconcile persona header names with emitted headers**, and delete the stale
    `GRAPH_BOOST_KEYWORDS` section from `MEMORY_AND_CONTEXT.md`.
13. **Add "don't ask what memory already answers"** to the clarification guidance
    in `soul.md`. It currently says to interpret before asking, but never says to
    check injected context first. Cheap defense-in-depth for this exact failure.

### P4 — evals, so this cannot regress — **DONE**

14. **A deterministic recall lane.** Pairs of (query → fact that must appear in
    the boost block), asserted offline with no model calls — it is pure ranking.
    `scripts/probe_memory_recall.py` is the manual version; the date query is
    case #1. Fits alongside the existing `evals/datasets/memory_selection.jsonl`
    and `evals/graph_memory_probe.py`.
15. **Fail loudly on a placeholder graph**, so a worktree eval cannot silently
    measure recall against the example template.

### Later — finish bi-temporality

Edges already carry `valid_from` / `valid_to`; both are `None` everywhere. The
Zep/Graphiti result (63.8% vs 49.0% on LongMemEval for the temporal-graph
approach over vector-first) suggests finishing this is worth real points, but it
is #656 territory and strictly lower priority than P0-P2.

---

## Sources

- [Agent Memory: How to Build Agents That Learn and Remember — Letta](https://www.letta.com/blog/agent-memory/)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956)
- [Mem0 vs Letta (MemGPT): AI Agent Memory Compared](https://vectorize.io/articles/mem0-vs-letta)
- [Mem0 vs Zep (Graphiti): AI Agent Memory Compared](https://vectorize.io/articles/mem0-vs-zep)
- [State of AI Agent Memory 2026 — Mem0](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Best AI Agent Memory Frameworks in 2026 — Atlan](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)


---

## Follow-up round (same session)

Building the eval surfaced failures the original audit missed, because it probed
categories the reported bug never touched.

| Found | Root cause | Fix |
|---|---|---|
| Behavior directives injected on coding questions | Directives stored as retrievable fact nodes; semantically bland, so they weakly matched everything | New `## _behavior` reserved section — always on, never retrieved |
| "How tcp handshakes **work**" pulled in every career fact | `score = max(score, 0.55) + 0.15` floored topic-matched facts to a flat 0.70, above any threshold, with internal ranking destroyed | Multiplicative `_TOPIC_HINT_BOOST`; dropped bare "work" from the career hints |
| "What still needs booking" returned the wedding date; the dog's name was unreachable | Whole section stored as one node, then truncated to 120 chars | One fact node per line |
| Editing `personal_knowledge.md` did nothing without deleting the graph | Seeding only ran on an empty graph | Seed facts reconciled from markdown on every init, preserving `forget()` |
| "Who do I work with" returned nothing | Life-area shortcuts seeded hardcoded node ids for one specific user's employer/university | Seed the ego node and let the typed walk find them — works on any graph |

**Known limit, deliberately not chased:** some negatives are genuine embedding
near-collisions ("refactor the tool **selector**" vs "internal **tooling**" scores
0.257, above a real positive at 0.247). Neither a scalar threshold nor a
relative-margin gate separates them. Those cases carry an explicit
`allow_bullets: 1` and a note in the dataset rather than a ranker tuned to fit one
fixture.

**Still open:** `graph_rag_client.py` is ~1400 lines, past this repo's 1000-line
ceiling — retrieval/ranking wants to split out from graph CRUD. And several
Matthew-specific strings remain in `ego_triggers` and in doc examples across
`personal_knowledge_parse.py` / `sleep_cognify.py`, which matters for going public.

---

## What shipped after this audit (epic #655)

This document is a point-in-time record of the July 2026 audit. Six of its eight
follow-ups have since landed on `dev`; the two paragraphs of "still open" above
are largely resolved. Current state:

| # | Shipped | Notes |
|---|---|---|
| #658 | `forget()` scores candidates and acts on ONE | Was: invalidated everything over a flat 0.4 overlap, divided by query length, returning a bare bool. Now symmetric Jaccard + a decisive-margin gate; an ambiguous description invalidates nothing and returns the candidates. |
| #657 | Runtime saves join the typed graph | `ingest()` used to create a prose node only, so everything learned at runtime was invisible to the typed walk. Closed-vocabulary extraction now attaches entities/edges. No LLM on the write path. |
| #656 | Temporal edges + supersession | **Closes the 0.43 blind spot below.** `employed_by` + `SINGLE_VALUED_RELATIONS`; a new value stamps `valid_to` on the old edge instead of racing it. Expired edges are reachable only by an explicitly past-tense query. |
| #659 | Entity attributes on walk lines | `Alex -attended-> Northgate (in Corvallis, Oregon)` instead of a bare pair of names. Ego attributes deliberately never rendered — the core profile already carries the user. |
| #661 | Keyword tables generalized | Type-driven seeding replaced the per-user node-id branches. No proper noun belonging to a real person remains in `graph_retrieval.py`. |
| #660 | Live write-behaviour lane | `--memory-write-live`. See the finding below. |

### The measured blind spot is closed

The 0.43-similarity row in the supersession table (an employer change, where the
changed value dominates the sentence) is no longer a known limit. It is caught
**structurally** — a single-valued typed relation knows it replaces the old value,
where the embedding only ever suspected it. No threshold was moved.

### The module-size note is resolved

`graph_rag_client.py` is under the ceiling, split into single-purpose neighbours:
`graph_retrieval.py` (ranking), `graph_backup.py` (Supabase mirror),
`graph_forget.py` (#658), `graph_runtime_relations.py` (#657/#656),
`graph_always_on.py` (core-profile/behavior reads).

### The new finding: writes are biased OFF

The live lane measured what the offline probe structurally cannot — whether the
model *calls* save. It does not, reliably: **0/3** on durable facts stated in
passing, while scoring **5/5** on correctly ignoring noise. The model acknowledges
the fact conversationally and calls no tool. The write gate is a floor on write
quality; it cannot help with writes that never happen. Tracked in **#674**.

### Still open

- **#662** — rebalance the per-turn context budget (measure after #659, which has landed).
- **#663** — strip the remaining real-name strings from docstrings, prompt examples and test fixtures. `graph_retrieval.py` is already clear; what is left is mechanical.
- **#675** — long in-process eval lanes die mid-run (VRAM swap thrash + orphaned MCP fleets).
