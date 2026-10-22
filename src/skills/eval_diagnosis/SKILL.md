---
name: eval_diagnosis
description: "Post-mortem a failed (or weak) run of Chad from its trace — find the behavioral root cause and propose a concrete fix. Use when asked to 'diagnose this eval failure', 'why did this run go wrong', 'analyze this trace', or to review Chad's own tool/delegation/prompt behavior. Reads the full trajectory (reasoning, tool calls, delegations, results), verifies whether a capability already exists before calling it missing, and proposes a prioritized fix by type — analysis only, no implementation unless asked."
version: 1.0.0
license: MIT
tags: [evals, diagnosis, self-improvement, tools, prompts, harness]
---

# Eval Diagnosis — post-mortem a Chad run

You are doing a **post-mortem on a past run of Chad** (yourself) to improve Chad's own behavior —
the same thing a developer does when a test fails. Read the run's full trace, find the **root
cause**, and propose a concrete fix. You are the **diagnostician for the Chad-on-Chad loop**
(issue #358) — not the offline LLM-as-judge in `evals/judges.py`.

**Split:** Gemma/rubric judges **score and classify** trajectories. This skill **proposes
generic harness/prompt/tool fixes** (and optionally implements when asked). Do not re-attempt
the user's original task. Append lasting harness mistakes to
[`evals/HARNESS_LESSONS.md`](../../../evals/HARNESS_LESSONS.md) when you close a class of failure.

This is behavioral debugging, not coding. The fix is very often a **prompt** or a **tool-selection
hint**, not code.

## Read the whole trajectory — reasoning first

A trace has: the original prompt, per-agent **reasoning**, every **tool call with its params**,
every **tool result**, the **plan** (if any), the **final response**, `stop_reason`, and timing.
Read them all, but start with the **reasoning** — behavioral bugs live in *why* the model did
something, not just in which assertion tripped. Attribute reasoning to the right agent
(Supervisor vs. a delegated specialist like Traversal/Communication/Browser) — a bug in a
sub-agent's thinking has a different fix than one in the Supervisor's.

If the trace is missing something you need (e.g. you can't tell which tools were *available* that
turn), **say so** — an incomplete trace is itself a finding worth flagging.

## What to look for (failure taxonomy)

1. **Wrong / missing tool selection** — the right tool existed but wasn't chosen, or a wrong one was.
2. **Missing search domain** — searched one place but not the obvious other (e.g. Google Drive but
   never the **local filesystem**, or vice versa). Watch for an agent *pre-committing* to one domain.
3. **Prompt bias or contradiction** — a persona/agent prompt steered the wrong behavior, over-weighted
   one signal (e.g. an email in context → "it must be in Drive"), or contains conflicting instructions.
4. **Reasoning spiral / context bloat** — many near-identical calls (e.g. 20+ keyword variations),
   long circular reasoning, no termination or pivot. Count the repeats; that's your evidence.
5. **Capability gap vs. unused capability** — see the hard rule below.
6. **No completion** — the turn ended without actually answering (`stop_reason`, a final message that
   trails off mid-action, or a "here's what you could do next" non-answer).
7. **Delegation error** — wrong specialist chosen, or the Supervisor's brief to a specialist
   *pre-constrained* it (e.g. told Traversal "search Drive" when the task needed an any-location search).

## Hard rule: VALIDATE every claim with a tool call — never make it up

You are NOT handed the correct answer, and you must not invent facts to fill the gap. Every claim
in your diagnosis must be something you **actually verified**, not assumed or remembered:

- **Before writing "missing capability"** — you MUST actually call `list_skills` and
  `search_for_tool` for the capability you think is missing. Do not write "I checked list_skills"
  unless you literally called it this turn. Example: a `pdf` skill exists for reading/handling
  PDFs, so "Chad can't read PDFs" is almost always a **selection** problem (a/b), not a missing
  capability (d). If a tool/skill exists but wasn't used → selection/prompt fix. If your own
  `search_for_tool` call genuinely returns nothing → then, and only then, it's (d).
- **Before writing "the run found the WRONG thing"** — the trace only shows what the run *did*,
  not whether it was right. To claim it missed a better answer, go **investigate**: actually
  search the place the run skipped (e.g. run a local-filesystem search yourself), and confirm a
  better-matching file/result exists. If you can't verify a better answer exists, say so — don't
  assert it.
- **Before blaming a prompt** — read the actual prompt (`prompts/persona/<agent>.md`) and quote the
  line that produced the behavior. Don't guess at what the prompt says.

Read-only investigation during a diagnosis is not just allowed, it's REQUIRED — that's how you
validate instead of fabricate. (You still must not run the original user task or edit anything.)
A claim you did not verify with a tool call or a file read is a guess; either verify it or label
it explicitly as an unverified hypothesis.

## A fix must be GENERIC — never overfit to this one case

"Fixing" a failure does NOT mean hardcoding language into a prompt or tool so this single case
passes. That is cheating the eval, not improving Chad — it makes the report green while the
underlying behavior is unchanged for every other case.

- ❌ Overfit: "In `agent.md`, add: *if the user asks about an annual report, also search the local
  filesystem.*" (names this case; helps nothing else.)
- ✅ Generic: "In `agent.md`, add: *when a requested file's location is unspecified, search BOTH
  the local filesystem and the cloud before concluding it's missing.*" (solves the whole class.)

Test every proposed fix against this bar: **if it mentions the specific file, person, entity, or
phrasing from this run, it is overfit — generalize it to the repeatable problem underneath.** The
goal is to solve a generic but recurring problem so that ALL similar runs improve.

## Categorize each fix

- **(a) tool/selection hint** — a tool that should have been bound/used but wasn't, or was used wrong.
- **(b) prompt revision** — a persona/agent prompt that caused wrong behavior or contains a contradiction.
- **(c) context bloat / inefficiency** — wasted calls or a reasoning spiral; propose a limit/pivot.
- **(d) missing capability** — a tool/skill that genuinely does not exist (only after verifying).
- **(e) delegation / routing fix** — wrong specialist, or a pre-constraining delegation brief.

## Where fixes live

- **Prompts** — `prompts/persona/<agent>.md` are the **live** prompts (e.g. `agent.md` = the main
  Supervisor). A behavioral/bias/contradiction fix usually edits one of these. NOTE: a per-user
  overlay at `config/persona/<agent>.md` WINS over the baseline at runtime — so to actually change
  observed behavior you must edit whichever one is effective, not just the baseline.
- **Tool selection** — `src/infrastructure/tools/selection/`.
- **Delegation routing** — `src/infrastructure/tools/delegate_routing.py`.
- **Skills** — `src/skills/<name>/SKILL.md`.

## How to EXECUTE the fix — pick the right kind of change

Classify every proposed fix into one of these, because they are executed very differently. The
whole point is that Chad Code can then actually carry it out the way we want — or, when it's too
big to do inline, that it gets TRACKED instead of half-built.

1. **Prompt / behavior change** (fix types a, b, c, e — a routing/selection hint, a persona
   revision, a spiral guardrail). → **Edit the effective persona prompt.** This is the common case,
   it's cheap and reversible, and Chad Code can make it directly. Keep it GENERIC (see the overfit
   rule above) and terse, matching the surrounding rules.
2. **New / expanded capability** (fix type d — a tool or skill that genuinely does not exist, e.g.
   "read a local PDF and return its text"). → **Do NOT half-build a new tool inline, and do NOT
   fake it with a prompt.** A prompt can't create a capability. Always do BOTH of these, not one:
   - **File a GitHub issue** that describes the capability, why it's needed (cite the failing run),
     and where it would live — filing the issue IS a legitimate, honest outcome of the diagnosis.
   - **Add a graceful-fail prompt patch** so Chad stops spiraling on this class of task until the
     capability exists: name the specific missing capability out loud and suggest the user could
     have it added, instead of silently failing or retrying workarounds (this generalizes the
     always-on rule in `prompts/persona/tools.md`, not a one-off line naming this case).
3. **Real bug fix** (a code defect — wrong wiring, an off-by-one, a broken tool). → **Edit the
   code** (and ideally add/adjust a test). This is Chad Code's strongest mode; give it the exact
   file/symbol and the expected behavior.

Decision rule: a small prompt/routing/selection change → make it. A new tool, a new skill, or an
architectural change → **file an issue** (with the eval evidence) rather than jamming a fake fix
into a prompt. "It needs a capability we don't have yet" is a correct finding, not a failure.

## Output format

A **prioritized** list, most critical root cause first. For each:

> **Problem N — <short title>** (priority)
> - **Evidence:** what in the trace shows this (name the agent, tool, the count of repeated calls, the quote).
> - **Root cause:** the actual why.
> - **Fix type:** (a)–(e).
> - **Proposed fix:** exactly what to change, **precisely how it would work**, and **where it lives**
>   (which prompt/file/tool). Concrete enough that someone could implement it without guessing.

## Honesty

- If the run was actually fine, or the failure is **environmental / latency / a harness bug** (a
  hung eval, an unconfigured integration, a timeout), **say so** — do not invent a code bug.
- The correct *answer* to the user's task may be legitimately ambiguous (data changed, multiple
  valid files). Judge the run on **how it searched and reasoned**, not on hitting one exact answer.
- Don't propose a broad refactor when a one-line prompt or selection hint is the real fix.
