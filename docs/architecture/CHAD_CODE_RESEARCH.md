# Chad Code research spike — harness scaffolding for local-model coders (#582)

> Historical evidence/research. For current behavior use [CHAD_CODE.md](CHAD_CODE.md).
> Results and proposals below describe their original measurement period.

**Part of:** [#569](https://github.com/MatthewLoyed/AIAssistant/issues/569) · **Status:** research complete, quick wins to be filed as follow-ups.

**Premise (unchanged from [CHAD_CODE.md](CHAD_CODE.md)):** Chad Code runs a *local* model, which is
structurally weaker than frontier cloud models. The leverage is in harness scaffolding, not the
weights. This spike surveys what OpenHands and the broader agent-harness ecosystem offer, and tags
each technique **adopt** (do it, cheap and clearly worth it) / **trial** (probably worth it, needs
an eval to confirm) / **skip** (not worth it for Chad Code specifically, with reason).

The `evals/chad_code_e2e_probe.py` explore-and-report tier already found the sharpest real pain
point: on a 6,255-file repo, the agent burned 412K tokens and ~19min doing raw `find`/`grep`/`cat`
exploration with **no repo-level retrieval** — "cost scales with codebase size, not task
complexity." Everything below is read against that finding.

---

## 1. Large-codebase exploration

| Technique | Tag | Notes |
|---|---|---|
| **Repo map (Aider-style, PageRank over tree-sitter symbol graph)** | **trial** | Aider builds a directed graph of symbol defs/refs across the repo, ranks it with personalized PageRank, and packs the most relevant slice into a fixed token budget (default ~1,024 tokens) instead of raw file dumps. This is exactly the gap the explore-and-report eval exposed. Building this in-house is a real project; see next row for a shortcut. ([aider.chat/docs/repomap](https://aider.chat/docs/repomap.html), [aider.chat/docs/ctags](https://aider.chat/docs/ctags.html)) |
| **Local code-graph MCP server (CodeGraph / Serena-class tools)** | **adopt (in progress)** | Don't build a repo map from scratch — the `@colbymchenry/codegraph` MCP already used in this dev environment is exactly this category: a local SQLite symbol graph (search/explore/callers/callees/impact) served over MCP, no cloud, no embeddings API. It turns "grep the whole tree" into one `codegraph_explore` call for a small local model — directly attacking the 412K-token ceiling. **Integration reality (corrected):** codegraph is a **stdio** MCP (`codegraph serve --mcp --path /repo --no-watch`), and the base OpenHands image has no node/npx, so it can't be wired the way the *hosted-HTTP* Context7 MCP is. The chosen approach is to **bake codegraph into a small custom image layered on the base and run it as an in-container stdio MCP over the read-only `/repo` reference mount** — no host↔container bridge, no path mismatch (codegraph returns `/repo`-relative paths the agent already sees). Gated on a `reference` repo being mounted (the explore-and-report case), since that's the only scenario with the token problem. Paired with a `large-repo-explore` skill that tells the agent to prefer the graph tools over raw `find`/`grep`. Highest-leverage change from this spike — but a real mini-feature (custom Dockerfile), not the config one-liner an earlier draft implied. |
| **LSP-backed navigation (Serena-class)** | **skip (for now)** | Answers single-hop "where is X defined/used" well, but a code-graph MCP already covers that plus multi-hop (impact radius, callers-of-callers) that a small model can't chain together via repeated tool calls anyway. Redundant with the adopt above; revisit only if the code-graph MCP proves insufficient for a language it doesn't parse. |
| **"Condensation"/summarization of the explored context so a small model isn't fed the whole tree** | **trial** | This is the *history* condenser (section 3) applied mid-exploration rather than mid-editing — same mechanism, different trigger. Worth a probe once the code-graph MCP is in place, since it may make this moot (less raw text to condense in the first place). |

## 2. OpenHands microagents / skills

Already substantially adopted — Chad ships `code-review` and `github-workflow` skills via
`load_skills_from_dir(/chad/skills)` (CHAD_CODE.md §"What's built"). Findings that refine this:

| Technique | Tag | Notes |
|---|---|---|
| **Terminology/location: `.openhands/microagents/` is deprecated in favor of `.openhands/skills/`** | **adopt** | Purely a naming/location fact, not new work — Chad already uses the V1 "skills" vocabulary (`AgentContext(skills=...)`), so no change needed, just confirms we're on the current path, not the deprecated one. |
| **`trigger`-based conditional skills (keyword-matched, only loaded when relevant) vs. always-on skills** | **trial** | Chad's two skills are both always-on (no `trigger` frontmatter). As more task-type skills get added (e.g. a future "flask-api" or "test-first" skill), keyword triggers keep the system prompt from bloating with irrelevant SOPs on every job — worth using going forward rather than defaulting every new skill to always-on. |
| **`AGENTS.md` / `CLAUDE.md` auto-discovery at repo root** | **adopt (already free)** | OpenHands repo skills auto-discover `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` in the workspace root. Since Chad Code's workspace is a fresh empty dir per job today, this has no effect yet — but if/when Chad Code is pointed at a *real* repo (vs. a scratch workspace) to fix a bug or extend existing code, that repo's own `CLAUDE.md` will be picked up automatically with zero integration work. Worth remembering, not building. |
| **Per-task-type Chad-authored skills** (the issue's explicit ask: "whether we can author Chad-side ones per task type") | **trial** | Concrete candidates: a `test-first` skill nudging "write the test before the implementation" for tasks with a clear success criterion, and a `large-repo-explore` skill that tells the agent to reach for the code-graph MCP tools before `find`/`grep`. The second one is the cheapest way to make item 1's MCP wiring actually get *used* by the model instead of ignored. |

## 3. Context management / condensers

| Technique | Tag | Notes |
|---|---|---|
| **`LLMSummarizingCondenser`** — OpenHands SDK's default condenser: keeps recent events verbatim, LLM-summarizes older ones once history exceeds `max_size` (event count), with `keep_first` events (e.g. system prompt) always preserved | **trial** | Chad Code's proven tiers (1–3, edit-existing-code) all finished in single-digit-minutes with no condensation need. The explore-and-report tier is the one that would actually exercise this (19min, 412K tokens — almost certainly crossed whatever the default `max_size` is). Worth enabling with logging to see if it triggers and whether the summarized history still lets the agent finish coherently, rather than assuming it's needed. |
| **Agent-requested condensation** (model can emit a `request_condensation` tool call itself) | **skip** | Requires the model to recognize its own context pressure and choose to call a tool for it — a level of self-monitoring small/mid local models are unlikely to reliably exhibit. Threshold-based auto-condensation (`max_size`) doesn't depend on the model behaving well, so it's the safer default for this harness's model tier. |
| **Condensation only triggers past a size threshold, preserving prompt-cache prefix stability below it** | **adopt (design constraint to respect)** | If/when the condenser is wired in, don't be tempted to condense on every turn "just in case" — that defeats prefix caching for the (currently short) proven tiers. Threshold-gated is the right default, matching the pattern already used for KV-cache-friendly prompting elsewhere in this codebase ([harness-perf-parallel-and-ttft memory]). |

## 4. Prompt scaffolding (planning, verification, self-check)

| Technique | Tag | Notes |
|---|---|---|
| **`task_tracker`** | **already decided — no change** | A/B'd and defaulted OFF for small models per CHAD_CODE.md; this spike found nothing in the wider ecosystem that contradicts that call. OpenHands' own docs don't claim task-tracking scaffolds help smaller models specifically — the benefit is assumed for larger/frontier models with better self-monitoring. Revisit only if Chad Code moves to a materially larger coder model. |
| **Iteration budget tuning (`max_iterations`)** | **adopt** | Literature on a comparable model class (Devstral-Small) found **~50 iterations is the efficiency/performance sweet spot**, and OpenHands' own default (~100) shows only marginal gains from doubling to 200 — sometimes *worse* results (more room to wander off-task). Chad Code's proven tiers finished well under any reasonable cap (155–335s), but the explore-and-report tier's 19min/412K-token run is exactly the shape that benefits from a lower ceiling forcing earlier convergence. Worth trying a per-task-class default (e.g. lower cap for build/fix tasks, higher for explore-and-report) rather than one global `CHAD_MAX_ITER`. |
| **Hard cost/retry ceilings**: `LLM_NUM_RETRIES` cap + an accumulated-cost cutoff, alongside `max_iterations`, enforced by the AgentController as a supervisor | **adopt** | Chad Code already has `max_iterations` and a wall-clock timeout (CHAD_CODE.md's "hardened" list). A retry-count cap and a token/cost ceiling per job are the two pieces this survey found that aren't explicitly confirmed as present — cheap, mechanical additions that guard against a stuck job silently burning tokens/VRAM-time rather than failing fast. Worth a quick grep of `chad_headless_runner.py` / `openhands_dispatch.py` to confirm before filing as a gap. |
| **Verification loops / self-check** | **already adopted** | The `code-review` skill is Chad's existing answer to "no built-in verification signal," and the proven tiers show the base `terminal`+`file_editor` toolset already does self-correcting test loops without extra scaffolding (Tier 2: agent's own test failed, self-corrected, re-ran green). Nothing found here beats what's already shipped; the real gap (tasks *without* a runnable test) is still open per CHAD_CODE.md and isn't solved by any technique surveyed — it needs either a task-framing convention (always ask for a test/runnable check) or a different verification mechanism entirely, which is out of scope for this spike. |

## 5. Model-specific tuning

| Technique | Tag | Notes |
|---|---|---|
| **`native_tool_calling=False` for Hermes-style local models** | **already adopted, confirmed correct** | CHAD_CODE.md already documents this as "mandatory." This spike's research on the Hermes function-calling format (`<tool_call>`/`</tool_call>` ChatML tokens, not native OpenAI `tool_calls`) confirms *why*: LM Studio passes the tagged text through unparsed unless OpenHands is told to parse it as text, matching the exact failure mode already diagnosed. No action, just confirms the existing fix is the right one, not a workaround to revisit. |
| **Temperature: 0 for the first attempt, small bump (~0.1) only on retries** | **trial** | Chad Code's current config isn't confirmed to set temperature explicitly (worth a quick check of `chad_headless_runner.py`). Deterministic-first-attempt is a cheap, well-evidenced default for coding-agent tasks; only worth changing if a config audit shows it's left at a library default that isn't 0. |
| **Per-task-class iteration/temperature profiles** (build vs. explore vs. fix) | **trial** | Ties together items 4's iteration-budget finding and this section — rather than one `CHAD_MAX_ITER`/temperature pair for all dispatches, a small per-task-class table (build: low iter cap, explore: higher cap + code-graph MCP, fix: default) would match tuning to the proven failure/success shapes already measured. |

---

## Priority read: what to actually do next

The single highest-leverage item is **giving the OpenHands coder the local `codegraph` MCP**, run
as an in-container stdio server over the read-only `/repo` mount (custom image — see section 1) —
it directly targets the one concrete, measured failure mode this project has (412K-token/19min
explore-and-report run on a large repo) and doesn't require any new eval infrastructure to try.
Everything else in this table is either already correct (no action) or genuinely needs a probe
before committing (tagged **trial**).

**Status:** the codegraph integration + `large-repo-explore` skill are being implemented off this
spike (custom Dockerfile + `_build_mcp_config` gating). The model-tuning adopts from sections 4–5
(`temperature=0` deterministic first pass, `num_retries` cap at 2 so a stuck local model fails
fast instead of retrying 8×) are done in `docker/openhands/chad_headless_runner.py`, env-surfaced
as `CHAD_TEMPERATURE` / `CHAD_NUM_RETRIES` with a graceful fallback.

## Suggested follow-up issues (not yet filed — confirm before creating)

1. Wire a local code-graph MCP (codegraph-class) into `chad_headless_runner.py`'s
   `_build_mcp_config()`, gated the same way Context7/GitHub are, + a `large-repo-explore` skill
   that tells the agent to prefer it over raw `find`/`grep`. Directly targets the explore-and-report
   token ceiling.
2. Probe `LLMSummarizingCondenser` against the explore-and-report tier specifically (the only proven
   tier long enough to plausibly trigger it) and confirm it doesn't degrade report quality.
3. Per-task-class `max_iterations` (and confirm/set temperature=0) instead of one global
   `CHAD_MAX_ITER`, informed by the ~50-iteration sweet spot found for comparable model sizes.
4. Confirm (via a config grep, not assumption) whether `chad_headless_runner.py` already caps
   `LLM_NUM_RETRIES` / has a cost ceiling; file a gap issue only if it doesn't.

## Sources

- [OpenHands: General Repository Microagents](https://docs.all-hands.dev/modules/usage/prompting/microagents-repo)
- [OpenHands skills/README.md](https://github.com/OpenHands/OpenHands/blob/main/skills/README.md)
- [OpenHands: Agent Skills & Context](https://docs.openhands.dev/sdk/guides/skill)
- [OpenHands: Context Condenser](https://docs.openhands.dev/sdk/guides/context-condenser)
- [OpenHands blog: Context Condensation for More Efficient AI Agents](https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents)
- [The OpenHands Software Agent SDK (arXiv 2511.03690)](https://arxiv.org/html/2511.03690v1)
- [Aider: Repository map](https://aider.chat/docs/repomap.html)
- [Aider: Improving GPT-4's codebase understanding with ctags](https://aider.chat/docs/ctags.html)
- [Code Intelligence Tools for AI Agents Compared](https://rywalker.com/research/code-intelligence-tools)
- [CodeGraph (codegraph-ai)](https://github.com/codegraph-ai/CodeGraph)
- [Hermes Function Calling](https://github.com/NousResearch/Hermes-Function-Calling)
- [Devstral: Fine-tuning Language Models for Coding Agent Applications (arXiv 2509.25193)](https://arxiv.org/html/2509.25193v1)
