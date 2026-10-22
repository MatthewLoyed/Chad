# Premature completion — why agent-lane turns stop without doing the work

Background for the `completion_check` mechanism in `evals/live.py` (issue **#566**,
problem 2). Written down because the obvious fix here is the one the field has already
measured as ineffective, and it would be easy to reach for it again.

## The failure mode

`code_agent` reads the target file, then ends the turn with "Task completed." having
produced no diff. This is not a Chad-specific quirk — it is a named, independently
replicated failure mode. Four research groups converged on it within a year under
different names: *premature termination*, *fixing correct code*, *inflated resolution
rates*.

The mechanism is **stopping-criterion misalignment**: the agent fires its done-token on
the first visible marker of progress (a file read, a test passing, a patch applied) while
the actual objective is unmet. SRI Lab found agents patch already-passing code >50% of the
time across multiple model families and 235 tasks.

The harness half of the problem is the more important half:

> A terminal message from the model — one with no further tool calls — ends the agent's
> turn. It does not mean the user's goal has been satisfied. The agent harness is
> responsible for checking whether the goal is actually complete, not simply whether the
> model has stopped emitting tool calls.

Our harness did check — but only against **plan progress**, and agent-lane turns carry no
plan. `total == 0`, so the nudge branch was unreachable for exactly the lane that needed it.

## What works (and what doesn't)

Ranked by the evidence in the sources below.

| Remedy | Evidence |
|---|---|
| **Observable state-tied stopping criteria** — anchor termination to real repo/test state, never to transcript claims like "all tests passing" | the general principle behind the rest |
| **Runtime-enforced verification** — the runtime injects the check with no opt-out, blocking termination until external state agrees | ForgeCode, 81.8% on TermBench 2.0 |
| **Reproduction-first prompting** — make the agent trigger the bug before patching | GPT-5.4 mini, 24% → 77% on correct-code detection |
| **Pre-completion checklists as harness variables** — stopping criteria as tunable harness config, not prose | LangChain, Terminal Bench 2.0 52.8% → 66.5% |

Counterproductive, per the same sources:

- Generic "be thorough" instructions with no observable hook.
- Longer reasoning chains — they defer the decision without changing it.
- Chain-of-thought prompting — masks incompleteness with more confident-sounding prose.
- Over-verification spirals, which trade this pathology for its inverse.

Note also that premature termination is strongly model-dependent (near-zero on the
strongest frontier models). On a local 30B coder it is a live concern, so the harness has
to carry the check rather than assume the model will.

## What we implemented

`probe_workflow(..., completion_check=...)` — a predicate over observable state, `() -> bool`
or `() -> (bool, gap_description)`, asked after any turn that ends without a pause:

- **Unsatisfied → nudge**, bounded by `MAX_CONTINUE_NUDGES`, with the gap named in the
  message. Naming the specific gap is the difference between this and "be thorough".
- **Still unsatisfied at the end → overrides `completed`.** The model's claim to have
  finished is not evidence that it did. The gap lands on `LiveTrace.completion_gap`.
- **A predicate that raises counts as satisfied.** Deciding "the model failed" on the
  strength of our own broken check would manufacture findings. The exception is recorded on
  the trace instead.

`coder_bench` supplies `git status --porcelain` over its disposable worktree.

Deliberately **not** the FAIL_TO_PASS tests: making the stop condition the graded outcome
would let the agent tune against the answer key — the same reason SWE-agent's `submit` does
not run tests. "A change exists" is the weakest honest signal that separates work from
narration.

An unsatisfied predicate deliberately does **not** set `LiveTrace.error`. `error` means
"the probe itself failed", and `coder_bench.score()` maps any error straight to
`backend-error` — which reads as infrastructure trouble and masks the `no-op` /
"empty diff — no edit attempted" bucket it already computes. **The model doing nothing is a
result, not a harness failure.** The first version of this got that wrong and only a live
bench run showed it; `tests/unit/test_live_completion_check.py` now pins it.

## Measured on a live run (2026-08-08)

`telegram_routing_state_local` against `qwen/qwen3-coder-30b`, the task #566 cites:

| Round | Tools called |
|---|---|
| 1 | 0 |
| 2 (after nudge) | 0 |
| 3 (after nudge) | 1 |
| 4 (after nudge) | 0 |

Before this change the run ended at round 1 — `total == 0`, so the nudge branch was
unreachable. The nudges did move the model off zero, but not to an edit: the verdict is
still a no-op, now with the effort spent recorded alongside it. That is the honest
outcome, and it is the point — the harness measures the gap instead of hiding it.

The remaining failure is a **model/prompt** problem (a 30B coder that reads and narrates
instead of editing), not a harness one, and should be tracked as such.

## Sources

- [Premature Completion — AgentPatterns.ai](https://agentpatterns.ai/patterns/anti-patterns/premature-completion/)
- [How to Design Agent Loops with Verifiable Stop Conditions — MindStudio](https://www.mindstudio.ai/blog/agent-loops-verifiable-stop-conditions)
- [The Agent Loop Decoded — Oracle Developers](https://blogs.oracle.com/developers/the-agent-loop-decoded-three-levels-every-agent-engineer-must-know)
- [Feature Request: Add Stop Hook to prevent early agent termination — OpenHands/software-agent-sdk#1527](https://github.com/OpenHands/software-agent-sdk/issues/1527)
- [How to change/add a termination tool call — SWE-agent/SWE-agent#878](https://github.com/SWE-agent/SWE-agent/issues/878)
- [The OpenHands Software Agent SDK (arXiv 2511.03690)](https://arxiv.org/html/2511.03690v1)
- [EvoCode-Bench: Evaluating Coding Agents in Multi-Turn Iterative Interactions (arXiv 2605.24110)](https://arxiv.org/pdf/2605.24110)
