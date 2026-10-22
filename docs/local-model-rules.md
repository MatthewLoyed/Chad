# Local Model Rules

Simple rules for when local models are underperforming. Not theory — just what actually matters.

---

## The 4 Things That Kill Local Model Performance

### 1. System Prompt Too Long
**Rule: Keep it under 3,000 tokens.**

Local models start ignoring parts of long system prompts. If your prompt is 8k-12k tokens, the model is effectively skipping half of it. Unlike cloud models (Claude, GPT-4), local models aren't trained to follow long complex instructions reliably.

- Cut anything that isn't directly needed for the current task category
- Agentic tasks need different (shorter) prompts than conversation tasks
- If you must have a long master prompt, only send the relevant section to local models

---

### 2. Temperature Is Wrong for the Task
**Rule: Match temperature to what the model is actually doing.**

| Task | Temperature |
|------|-------------|
| Calling tools / following steps | 0.3 – 0.5 |
| Planning / reasoning | 0.6 |
| General conversation | 0.7 |
| Creative writing | 0.9+ |

High temperature (0.7-0.9) on multi-step tasks introduces variance at every step. By step 4, the model has drifted. Most "dumb" local model behavior is actually a temperature problem.

Never use greedy decoding (temp 0) with thinking/reasoning models — it causes repetition loops.

---

### 3. No Planning Step
**Rule: Force a plan before any multi-step task.**

Local models given a complex request immediately start improvising. They skip steps, conflate subtasks, and lose track of the goal halfway through.

Fix: require a plan first.
```
Before executing, write a numbered plan. Then execute step by step.
```

This single change has the largest impact on day-to-day task success rates. Coding benchmarks don't test this — which is why models can be 80% on benchmarks and still fail basic real-world tasks.

---

### 4. Model Answering From Memory Instead of Using Tools
**Rule: Explicitly mandate tool use for external data.**

Local models default to answering from training memory because that's what most training rewards. They'll confidently make up an email instead of reading your inbox, or invent a calendar entry instead of checking.

Fix: add this to the system prompt for agentic tasks:
```
For ANY task involving real-world data (email, files, calendar, web, contacts),
you MUST use a tool. Never generate from memory when a tool exists.
```

---

## How to Diagnose a Struggling Local Model

Work through these in order — most problems are one of the first three.

**1. Is the response ignoring part of the system prompt?**
→ Prompt is too long. Cut it down, or split into task-specific prompts.

**2. Is the response "creative" when it should be precise? Skipping steps? Drifting?**
→ Temperature too high. Drop it and retry.

**3. Did it attempt the task without making a plan first?**
→ No planning gate. Add a forced plan step.

**4. Did it answer from memory instead of calling a tool?**
→ Missing explicit tool mandate. Add it.

**5. Is it forgetting context from earlier in the conversation?**
→ Context window is filling up. The conversation history is too long for the model's practical limit. Summarize or compress earlier turns.

**6. Is tool calling formatted wrong or failing?**
→ The model isn't sure what format to use. Add a concrete tool call example to the system prompt. One example beats three paragraphs of instructions.

**7. Is it performing fine on short tasks but failing on long ones?**
→ Multi-step coherence issue. Enforce a checklist/scratchpad pattern: after each step, the model states what it just did and what's next.

---

## Qwen3 Specific

- **Thinking mode** (`/think`): use for planning, complex reasoning, ambiguous tasks. Temp must be ≥ 0.6.
- **No-think mode** (`/no_think`): use for fast replies, simple tool calls, summaries.
- Mode persists across turns — set it at the system level, don't let it be user-controlled.

---

## LM Studio Infrastructure Gotchas (not model behavior — the runtime under it)

These look like "the model is broken / the harness is hanging" but are actually LM Studio/VRAM
issues one layer below the model. Check these BEFORE assuming a prompt or harness bug when a
local turn hangs, times out, or silently produces a degraded/wrong-model response (issue #216).

**1. GPU Offload set to "Highest"/"Max" aborts the whole model load under VRAM pressure.**
"Highest" maps internally to `n_gpu_layers=999999` (offload everything, no partial-CPU-offload
fallback). If free VRAM is tight at load time — e.g. Chad is mid-swap between the main model and
a specialist, or something else on the machine is using the GPU — llama.cpp aborts the load
outright instead of degrading gracefully: `Failed to load model "X". Error: Engine protocol
startup was aborted.` / `failed to fit params to free device memory: n_gpu_layers already set by
user to 999999, abort.` Check LM Studio's own log at `~/.lmstudio/server-logs/<month>/<date>.log`
for this exact string — it's the tell. Fix: set an explicit GPU-layer count a bit under the
model's max (not "Highest") for every model Chad juggles, so a tight-VRAM moment falls back to
partial CPU offload instead of crashing the load. Even a modest explicit number (e.g. 28) can
still occasionally abort during a rapid swap and self-recover on the next attempt — that's normal
and not a bug, as long as it doesn't hang.

**2. Keep "Only Keep Last JIT Loaded Model" ON — but ONLY if Chad never lets a model get
JIT-loaded in the first place.** LM Studio Developer tab → Server Settings. Chad's own swap
(`model_lifecycle.py`) is supposed to be the only thing managing residency — with Auto-Evict
OFF too, LM Studio ALSO tries to manage multiple resident models independently, piling them up
in VRAM until the load-time abort above hits. With it ON, LM Studio auto-evicts the previous
**JIT-loaded** model on every JIT load — reinforcing Chad's own swap logic IF and only if every
load Chad issues is the explicit `/api/v1/models/load` API. If anything ever falls back to a
plain inference call against a non-resident model (a JIT load), Auto-Evict does not wait for
that load to finish — it kills it mid-flight the instant a competing JIT load arrives. This was
the root cause of issue #592 (a ~65-120s per-turn latency regression) and is documented in full,
with a standalone reproduction script, in
[`MODEL_LIFECYCLE.md`](features/MODEL_LIFECYCLE.md#lm-studios-auto-evict-vs-jit-loading-592).
Also check **Max Concurrent Predictions** if a turn's request seems to vanish for a long time
before erroring — a low value can silently queue the main turn's request behind a background
call (e.g. the tool-selector's async pre-warm).

**3. "Engine Protocol" (beta) is a common source of `Failed to load model ... Engine protocol
startup was aborted`.** It's a separate-process runtime LM Studio shipped as opt-in beta and has
had multiple load-failure bugs reported upstream; newer LM Studio versions default it OFF. If
you see that exact error string and you're not deliberately testing the beta runtime, turn it
off (Settings → Developer) — Chad's own swap logic works with it either on or off, but the beta
runtime adds its own failure modes on top of Auto-Evict's.
- Strip `<think>...</think>` blocks from conversation history before the next turn or they compound and eat your context window.
- Q4_K_M quantization is production-ready. Q5_K_M if you have headroom.

---

## Two-Model Setup (Agentic + Speed)

If you're running a large model for agentic tasks and a small model for fast responses, the models need to actually be routed differently — not just swapped globally.

| Category | Model | Temperature |
|----------|-------|-------------|
| Planning, tool chains, autonomous tasks | Large (35B) | 0.6, thinking on |
| Conversation, quick replies, routing | Small (9B) | 0.7, thinking off |

Running both at the same temperature with the same system prompt wastes the entire point of having two models.

---

## What Benchmarks Don't Tell You

Coding benchmarks (SWE-bench, HumanEval) test single-shot, unambiguous, no-tool-required tasks. Real day-to-day agentic tasks are multi-step, ambiguous, require tool judgment, and require context coherence across turns.

A model at 80% on coding benchmarks can still fail basic email drafting, calendar planning, or MCP tool chains — because those require behavioral discipline (following instructions precisely, knowing when to use tools), not just raw reasoning. That discipline comes from your prompts and system design, not the model's size.
