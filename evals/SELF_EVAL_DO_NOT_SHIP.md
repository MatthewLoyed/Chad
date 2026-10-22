# Self-eval loop — DEV-ONLY, do not ship in a public release

The **Chad-on-Chad self-eval loop** (issue #358) lets the maintainer's machine grind on eval
failures overnight: dispatch the local coder → it edits a clone → a critic re-runs a real Chad
eval on the host → verdict → retry. It is a **maintainer development tool**, not a product
capability. It must **never** ship in the open-source release of Chad.

This is a design intent, not (yet) an enforced build step — the repo is currently private and
there is no public-release packaging script. When one is written, it MUST exclude the files below.
Until then this file is the single source of truth for "what is self-eval-only."

## Runtime is already inert in the product

The self-eval machinery only activates when the `CHAD_EVAL_SUITE` environment variable is set on
the Brain process before a dispatch (see `src/tools/dev/openhands_dispatch.py`). With it unset —
the default, and the only state a shipped product is ever in — there is **no critic, no bridge
call, and no self-eval skill**; `dispatch_coding_task` behaves as the ordinary Chad Code product.
So an accidental ship would be dormant, but the source still should not be there.

## Files to EXCLUDE from a public release

| Path | What it is |
|---|---|
| `evals/chad_code_bridge.py` | Host bridge: model swap + runs the eval against the coder's clone. |
| `evals/chad_self_eval_probe.py` | The escalating rung 0→3 probe driver. |
| `evals/SELF_EVAL_DO_NOT_SHIP.md` | This file. |
| `tests/unit/test_chad_code_bridge.py` | Bridge safety tests. |
| `docker/openhands/chad_eval_critic.py` | The critic that calls the bridge. |
| `docker/openhands/skills_selfeval/` | Self-eval-only coder skills (e.g. `chad-eval-diagnosis`). |

## Files that STAY (Chad Code proper — these DO ship)

- `src/tools/dev/openhands_dispatch.py` — the Chad Code product. It *references* the eval env
  vars but is inert without them; keep it.
- `docker/openhands/chad_headless_runner.py` — the coder entrypoint. Its critic import is wrapped
  in `try/except` and gated on `eval_suite`, so it runs fine with `chad_eval_critic.py` absent.
- `docker/openhands/skills/` — the product coder skills (code-review, github-workflow,
  large-repo-explore, web-search).

## If you excise the excluded files, verify

- `chad_headless_runner.py` still imports and runs (the critic block is `try/except` + gated).
- No shipped module does a top-level `import` of any excluded file — they are all referenced
  lazily / by env, never imported at module load.
