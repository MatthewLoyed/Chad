---
name: deep_research
description: "Structured, multi-source research SOP that produces a cited, synthesized answer instead of a single web hit. Use when the user wants to research a topic, compare options, make an informed decision (a purchase, a trip, a tool, a medical/legal question to look into), understand a subject in depth, or asks for 'the full picture', a 'writeup', a 'report', or 'dig into' something. NOT for a single quick fact ΓÇö use web_search directly for that."
version: 2.0.0
license: MIT
tags: [research, planning, synthesis, web, decision]
requires: web_search
---

# Deep Research

Turn a fuzzy question into a **structured, source-grounded answer**: plan the angles,
gather from multiple sources, cross-check, then synthesize with citations. This is the
difference between "I searched and pasted the first result" and "I researched it."

> This is a **methodology** skill, not a retrieval tool. Gathering is done by the
> `web_search` tool (quick) or the `research_agent` (heavy). This skill is the SOP that
> makes that gathering rigorous and the answer trustworthy.

## When to use this skill vs. when not to

| Situation | Do this |
|---|---|
| "What's the capital of Peru?" / one atomic fact | Just call `web_search` ΓÇö do **not** load this skill |
| "Which laptop should I buy for X?" / compare options | **This skill**, Tier 2 |
| "Research the pros and cons of X" / "give me the full picture" | **This skill**, Tier 2ΓÇô3 |
| "Write me a report on X with sources" | **This skill**, Tier 3 |
| Large, open-ended, many sub-questions | **This skill** ΓåÆ delegate to `research_agent` |

If you catch yourself about to run four or five `web_search` calls on the same topic,
stop and run this SOP instead ΓÇö it will be faster and the answer will be better.

## Pick a depth tier first

Decide the tier **before** searching. Announce it to the user in one line so they can
redirect ("actually just a quick answer"). `web_search` already returns ~10 results per
query, so tiers are about how many *queries* you run and how much you cross-check ΓÇö not
about starving yourself of sources.

- **Tier 1 ΓÇö Quick check (~1ΓÇô2 sources).** A fact that needs one confirmation. A single
  `web_search`; skim the top hit or two. You usually shouldn't have loaded this skill.
- **Tier 2 ΓÇö Focused research (~10ΓÇô20 sources).** A decision or comparison. Run the SOP
  below with **2ΓÇô4 `web_search` queries** (one per angle), reading the best results from
  each. This is the default when someone says "research X for me."
- **Tier 3 ΓÇö Deep report (~50ΓÇô100 sources).** Broad, high-stakes, or many sub-questions.
  **Delegate**: call `delegate_to_deep_research_agent` with the goal and the Phase-1 angle
  list, so the heavy multi-query gathering runs in a fresh context instead of bloating
  this conversation. Then present its findings using the output format below.

> Local-model note: keep this loop **linear**. Do not branch on every result. Finish the
> angle list, gather, then synthesize once. Re-planning mid-loop is where small models spin.

## Standard Operating Procedure

### Phase 1 ΓÇö Frame (no searching yet)
1. Restate the question in one sentence. If it's ambiguous or under-specified, ask **one**
   clarifying question (budget, timeframe, use-case) ΓÇö then proceed. Don't interrogate.
2. Write a short **angle list**: the 3ΓÇô6 sub-questions that must be answered to call this
   done. Example for "which laptop": *performance for my workload, battery, price,
   repairability, what reviewers dislike*.
3. Note the **freshness requirement**: does this need current info (prices, news, releases)
   or is durable knowledge fine? This decides how hard you lean on recent sources.

### Phase 2 ΓÇö Gather
For each angle, run `web_search` (or let `research_agent` do it in Tier 3):
- **Start broad, then narrow.** If a query returns nothing, do **not** repeat it — broaden it:
  drop exact-quote (`"..."`) operators and rare qualifiers, try the plain entity name. To get a
  specific business/place's own facts (prices, policy, menu), search its **name** to find the
  official site, then **open and read that site directly** rather than guessing at quoted phrases
  a search engine won't have indexed.
- Prefer **primary/authoritative** sources (official docs, the maker, standards bodies,
  peer-reviewed) over aggregators and SEO listicles.
- Get **at least two independent sources** for any claim that drives the conclusion.
- Track each source as you go: `[title] ΓÇö url ΓÇö the one fact you took from it`. You will
  need this for citations; reconstructing it later is wasted work.

### Phase 3 ΓÇö Cross-check (do NOT reconcile)
Sources will disagree. **That is expected and useful ΓÇö it is the point of multi-source
research.** Your job is to *report the range of views*, not to force them into one truth.

- **Never collapse conflicting sources into a single perspective.** If two sources
  contradict, present *both* ΓÇö attributed ΓÇö and let the divergence stand.
- **Do not loop trying to resolve a contradiction.** If sources conflict, note the conflict
  once and move on. Re-searching over and over to make everything agree is a known failure
  mode that stalls the whole turn. Contradiction is an *output* to report, not a problem to
  grind on until it disappears.
- **Stay neutral / unbiased.** Do not pick a side because it's more common, more recent, or
  more agreeable. If one source is genuinely more authoritative, you may *say so*, but still
  show the other view.
- Where sources genuinely **agree**, state it as settled.
- Flag anything you **could not verify** ΓÇö an unverified claim labeled as such is worth
  more than a confident guess.

### Phase 4 ΓÇö Synthesize
Answer the **original question**, organized by the angles from Phase 1 ΓÇö not by source,
and not as a link dump. Lead with the bottom line.

## Output format

```
**Bottom line:** <1ΓÇô3 sentence direct answer / recommendation>

**<Angle 1>**
<finding, with the tradeoff stated> [1]

**<Angle 2>**
<finding> [2][3]

**Where sources disagree:** <e.g. "Source [2] says X; source [4] says the opposite ΓÇö
[2] is the official maker, [4] is a user forum. Both are shown; you decide.">

**Caveats / unverified:** <what's uncertain, what you couldn't confirm>

**Sources**
[1] Title ΓÇö url
[2] Title ΓÇö url
```

Omit the "Where sources disagree" block only if there genuinely was no conflict.

Scale the shape to the tier: Tier 2 can be a few tight paragraphs; Tier 3 earns headers.
For a "report" deliverable, offer to render it (the `docx`, `pdf`, or `typst` skills).

## Quality bar (self-check before you present)
- [ ] Every conclusion-driving claim has ΓëÑ2 independent sources, or is labeled unverified.
- [ ] Conflicting sources are shown side-by-side, **not** reconciled into one view.
- [ ] You did not loop re-searching to resolve a contradiction ΓÇö you reported it and moved on.
- [ ] The answer maps to the user's actual question, not just "what the internet says."
- [ ] Sources are real URLs you actually opened ΓÇö **never fabricate a citation**.
- [ ] Recency matches the freshness requirement from Phase 1.

## Common pitfalls
- Γ¥î Pasting the first result and calling it research.
- Γ¥î Citing five sources that are all the same syndicated article.
- Γ¥î **Forcing conflicting sources to agree** / treating all sources as one perspective ΓÇö
  present the divergence instead.
- Γ¥î **Re-searching in a loop to make a contradiction go away** ΓÇö report it once and stop.
- Γ¥î Burying a recommendation under ten links ΓÇö lead with the bottom line.
- Γ¥î Running fifty inline `web_search` calls for a Tier-3 job ΓÇö **delegate** instead.
- Γ¥î Inventing a plausible-looking URL. If you didn't open it, it isn't a source.
