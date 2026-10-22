"""
Chad harness eval suite.

Token-efficient, dataset-driven evaluation of the AI harness so an agent can run
a large battery of cases and surface bugs, wrong tool/subagent wiring, prompt
contradictions, and context bloat — without manual probing.

Two lanes:
  • dry-run (default, FREE): assemble the real payload via SpyAdapter (no LLM call),
    assert on bound tools / token budgets / prompt contract.
  • judge (opt-in, cheap): one LLM-as-judge call per case to grade *semantic*
    quality (right tools? coherent? subagent reachable?). Cloud by default,
    `--judge local` for cost-free looping.

Entry point: `python -m evals.runner` (see evals/README.md / AGENTS.md).
"""
