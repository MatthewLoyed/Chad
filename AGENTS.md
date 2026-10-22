# Working on Chad

Chad is a Windows-first AI assistant: a headless Python Brain serves desktop
Limbs over WebSocket. Start with the task routes in
[the project map](docs/architecture/END_TO_END.md#find-the-existing-implementation).
Locate the existing implementation before proposing a new one.

- Use an isolated worktree/branch; open PRs into `dev`. Preserve unrelated work.
- Never start, stop, kill or restart the production Brain on port **8767**.
  Development uses **8788**: [production rules](docs/guides/PRODUCTION_BRAIN.md).
- Read [contribution and documentation rules](CONTRIBUTING.md) before editing.
  Update the owning guide instead of duplicating instructions or ideas.
- Keep secrets and personal data local; use fictional fixtures. See
  [private data](docs/guides/PRIVATE_DATA.md).
- Follow [module cohesion](docs/architecture/MODULE_COHESION.md); use composition
  and the existing EventBus. Python commands need `PYTHONPATH=.`.
- Use the [shared failure and limits policy](docs/architecture/FAILURE_AND_LIMITS.md)
  for errors, timeouts, retry caps and nudges; do not invent bare error strings.
- Run relevant checks and review the diff before committing or claiming success.
  For UI changes, exercise the actual UI; a type check alone is insufficient.
- For harness work, also read [evals/AGENTS.md](evals/AGENTS.md).

No private `RTK.md`, `.agent/` tree or code index is required. This tracked file
is the shared entrypoint; local tool settings may link here without copying it.
