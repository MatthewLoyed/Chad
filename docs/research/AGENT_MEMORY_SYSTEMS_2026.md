# How other agents do memory — and what we took from it

Research behind the July 2026 memory work (audit in
[MEMORY_AND_CONTEXT.md](../architecture/MEMORY_AND_CONTEXT.md), findings in
[MEMORY_AUDIT_2026-07.md](../architecture/MEMORY_AUDIT_2026-07.md), follow-ups in
[#655](https://github.com/MatthewLoyed/AIAssistant/issues/655)).

Written down because the *reasoning* is the reusable part. Several of these
findings are counterintuitive enough that we would have re-derived the wrong
answer from first principles — one of them ("tighten the tool schema") is the fix
we would have reached for by instinct and it is measurably wrong for local models.

---

## 1. The load-bearing facts must not be retrieved at all

**Letta / MemGPT** splits memory into *core memory blocks* — persona and human,
size-limited, embedded directly in the system prompt, always visible — and
*archival memory*, reached through an explicit `archival_memory_search` tool. The
OS analogy is deliberate: core memory is RAM, archival is disk.

**What we had:** the archival half only. Every personal fact, including "engaged
to <partner>, wedding <date>", went through similarity + threshold + item cap +
char budget on every single turn.

**What that cost:** asked to plan a date night, Chad asked what stage the
relationship was in. The fact was in the graph and had been ranked out.

**What we did:** `## _core_profile` — injected verbatim into every user-facing
system prompt, never retrieved, hard-capped at 800 chars. It lives in the *system*
prompt rather than the per-turn boost because it is stable across turns, so it
costs one prefill and is KV-cached after that.

The general rule, which is the part worth keeping: **if a fact is needed on most
turns, ranking it is a bug, not a feature.** Retrieval is for the long tail.

Sources: [Letta — Agent Memory](https://www.letta.com/blog/agent-memory/),
[Mem0 vs Letta](https://vectorize.io/articles/mem0-vs-letta)

---

## 2. Don't make a small model emit your schema

This is the finding most likely to be re-derived backwards.

**"The Constraint Tax"** ([arXiv:2605.26128](https://arxiv.org/abs/2605.26128))
measured structured-output constraints on small models. In a calendar tool-call
analogue, hard schema decoding kept **100% schema validity** while losing **43.5
points of executable accuracy**. Related work on XML prompting found the same
shape: "the syntax stays mostly correct because the formatting pressure is strong,
while the content inside the elements drifts."

The failure mode is nasty precisely because it is *silent*. Your logs show
well-formed JSON and a 100% parse rate. The values inside are quietly wrong.

Their prescription: **"reason free, constrain late"** — use the least intrusive
constraint that satisfies the downstream contract, and package the answer only
after the model has solved the task.

**What we did:** `manage_user_memory(save=...)` still takes a plain sentence. The
model is never asked to emit a structured memory object. It supplies prose — the
thing it is reliably good at — and answers the one genuinely judgment-laden
question: *is this worth remembering?* Normalizing, splitting, validating,
deduping and conflict-checking are all harness jobs, done deterministically with
no LLM call.

**The trap to avoid later:** when write quality looks bad, the obvious move is to
add fields to the tool schema. On a 9B model that will make the content worse
while making the telemetry look better.

---

## 3. Cheap geometry beats learned routing on the write path

**Mem0** originally used an LLM routing controller: inspect the top-k similar
existing memories, classify the candidate as ADD / UPDATE / DELETE / NOOP. **v3
retired it** for single-pass ADD-only extraction plus post-hoc dedup, because the
routing call was per-write regardless of novelty — cost scaled with total ingested
turns, not with how much was actually new.

**SAGE** ([arXiv:2605.30711](https://arxiv.org/pdf/2605.30711)) makes the same
point from the other direction: an embedding *novelty gate* — write only if
cosine similarity to the nearest existing memory falls below a threshold — beats
learned routing on cost, latency, and memory bloat, and acts as a natural
regularizer against unbounded growth.

**What we did:** `MemoryWriteGate` is this shape. One embedding lookup, no LLM
call, on both model-driven write paths (`manage_user_memory` and the
`[[learn: ...]]` directive — gating only one would have let the model bypass every
check by choosing the other).

Also from Mem0: **atomic facts at ingest**. Their extraction produces discrete
facts, resolves entities, timestamps them. We were storing a whole
`## section` as one node, so retrieval returned it as a unit and then truncated it
to 120 chars — the truncator decided which of your facts survived. Now one fact
per line, on both the seed and write paths.

Sources: [Mem0](https://github.com/mem0ai/mem0),
[Mem0 memory evaluation](https://docs.mem0.ai/core-concepts/memory-evaluation)

---

## 4. Contradiction needs time, not similarity

**Zep / Graphiti** ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)) is
bi-temporal: every fact tracks *valid time* (when it was true in the world) and
*ingestion time* (when the system learned it), four timestamps in all. Superseded
facts are **invalidated, not deleted**. Reported 63.8% vs Mem0's 49.0% on
LongMemEval.

We measured why this matters rather than taking it on faith. Supersession is only
visible to embeddings when the sentence *shape* survives the change:

| Change | Similarity to the fact it replaces | Detected? |
|---|---|---|
| "favorite foods are X" → "...are Y" | 0.73 | yes |
| wedding date moved | 0.77 | yes |
| **"now works at <different employer>"** | **0.43** | **no** |

0.43 sits inside the genuinely-new band (0.32–0.43), and its nearest neighbour is
the wrong node entirely. A relative-margin gate (top-1 minus median) doesn't
separate them either. **The information simply is not in the embedding** — the
changed value dominates the vector, so the new fact looks unrelated to the one it
replaces.

We stopped there deliberately and recorded the boundary in
`evals/datasets/memory_write.jsonl` with a `note`, rather than tuning the ranker
until the symptom hid. Doing it properly needs temporal typed edges —
[#656](https://github.com/MatthewLoyed/AIAssistant/issues/656). The
`valid_from` / `valid_to` fields already exist on our edges and are unused.

---

## 5. Salience and eviction are separate problems

The 2026 survey work converges on: *"age and recency handle noise; salience and
supersession handle facts, and most production systems need at least one of
each."* Importance gets scored at extraction time — by the extracting model or by
explicit user signal — and entries below a threshold are dropped.

We have neither yet, and at ~90 nodes it doesn't bite. It will. Worth revisiting
when the graph is large enough that the six-item budget is the binding constraint
rather than ranking quality.

There is also a security dimension we have not addressed: a taxonomy of memory
write channels with matching attack classes — *Salience-Driven Compaction
Poisoning*, *Policy-Conformant Fact Injection*
([arXiv:2604.16548](https://arxiv.org/html/2604.16548v2)). Chad's memory is
local-first and single-user, so the threat model is mild today, but "durable state
is the new attack surface" is worth remembering before any shared or
agent-writable memory lands.

Sources: [Mem0 — memory eviction and forgetting](https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents),
[Learning What to Remember (arXiv:2606.12945)](https://arxiv.org/pdf/2606.12945)

---

## 6. Where Chad sits now

| Capability | Field norm | Chad |
|---|---|---|
| Always-in-context core memory | Letta, Hermes | yes — `## _core_profile` |
| Atomic facts at ingest | Mem0 v3 | yes — one fact per line |
| Write-time novelty gate | SAGE, Mem0 | yes — `MemoryWriteGate` |
| Hybrid retrieval (semantic + keyword + graph) | Zep, Mem0 | yes — typed walk + fact similarity |
| Local-first, user-editable source of truth | OpenClaw, Hermes | yes — `personal_knowledge.md` |
| Non-destructive forget | Zep | yes — `valid: false`, recoverable |
| Bi-temporal validity / supersession | Zep | **no** — [#656](https://github.com/MatthewLoyed/AIAssistant/issues/656) |
| Typed extraction on runtime writes | Mem0, Zep | **no** — [#657](https://github.com/MatthewLoyed/AIAssistant/issues/657) |
| Salience scoring / eviction | Mem0, Letta | **no** — not yet needed |

---

## Method note

Every threshold in the memory stack was **measured on real and fictional data,
then recorded in the config comment** — never guessed, and never tuned until the
test passed.

That discipline is what caught the score-saturation bug (topic hints *flooring*
every matched fact to a flat 0.70, which put a whole topic above any threshold
with its internal ranking destroyed) and what stopped us from over-fitting the
ranker to a fixture on the two genuine embedding near-collisions.

Two habits worth keeping:

- **Negative controls are as important as recall.** Personal facts leaking into a
  coding question is the same class of bug as failing to recall them, and it is
  the one nobody notices. Our first eval run scored 34/36 on recall and 1/4 on
  negatives.
- **When a limit is real, write it down instead of hiding it.** A dataset case
  with `allow_bullets: 1` and a note explaining the measured overlap is more
  useful than a ranker contorted to score 100%.
