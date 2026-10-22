# Contributing to Chad

Thanks for your interest in Chad! This is a Windows-first, self-hosted AI
assistant. Contributions are welcome — please read this first.

## License of contributions

Chad is licensed under **AGPL-3.0** (see [`LICENSE`](LICENSE)). By submitting a
contribution you agree that it is licensed under the same terms.

## Branching & workflow

We do **not** commit directly to `master`.

- **`master`** — stable, release-tagged. Only updated by merging `dev` and
  cutting a tagged release. Protected: no direct pushes.
- **`dev`** — the integration branch. All day-to-day work merges here first.
- **Feature branches** — branch off `dev`, named `feature/<short-desc>`,
  `fix/<short-desc>`, or `claude/<slug>` for agent sessions.

Flow: `feature/*` → PR into `dev` → CI green → merge. Periodically `dev` is
promoted to `master` and tagged (see [`docs/RELEASING.md`](docs/RELEASING.md)).

### Pull requests

- One scope per PR. Reference the issue: `Closes #N` / `Refs #N`.
- CI must pass before merge (see below). Fix failures; don't skip hooks.
- Keep modules to one reason to change. Length itself isn't the problem — a flat route
  table or registry can be long and healthy; a single class carrying shared mutable state
  is a problem well before it looks big. See `CLAUDE.md.example`. Enforced as a ratchet by
  `tests/unit/test_module_cohesion.py`: if it fails, split the code rather than re-baselining
  it. `python scripts/dev/regen_cohesion_baseline.py` is for locking in an *improvement*.
  See [`docs/architecture/MODULE_COHESION.md`](docs/architecture/MODULE_COHESION.md).

## Before you push — run the free checks locally

These checks use no model tokens or service credentials:

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pytest tests/unit/
.\.venv\Scripts\python.exe -m evals.runner --prompt-audit           # prompt hygiene
.\.venv\Scripts\python.exe -m evals.runner --tool-contract          # tool UI/formatter contract
.\.venv\Scripts\python.exe scripts/dev/check_docs.py
```

Tool-selection and database-backed replay evaluations are separate opt-in checks;
they are not part of this repository's credential-free CI.
The [Windows CI workflow](.github/workflows/evals.yml) owns clean test-environment
setup: CPU PyTorch, the Transformers 4 API required by Nomic's custom model,
ReportLab for PDF fixtures and Playwright Chromium for animation tests.

New native tools **must** ship a display formatter + an entry in
`src/clients/web/src/utils/tool-ui.tsx` (copy `src/tools/example_tool.py.example`).

## Never commit private data

This repo is public. See [`docs/guides/PRIVATE_DATA.md`](docs/guides/PRIVATE_DATA.md).

- Secrets live in `config/secrets.yaml` (gitignored) — copy from `secrets.yaml.example`.
- No real names, emails, tokens, health info, or personal URLs in tracked files.
  Use placeholders (`the user`, `Alex Example`, `your-app.onrender.com`).
- Tests and fixtures use **fictional** data only.

If you find a real name/email/token in a tracked file, that's a bug — fix it.

## AI coding tools (optional)

`CLAUDE.md`, `.agent/`, and related paths are **gitignored** maintainer workspace files.
Copy `CLAUDE.md.example` → `CLAUDE.md` locally (`.\scripts\setup\bootstrap_dev_workspace.ps1`).
See [`docs/guides/DEV_WORKSPACE.md`](docs/guides/DEV_WORKSPACE.md).

## Reporting bugs & security issues

- Regular bugs → open a GitHub issue.
- Security vulnerabilities → **do not** open a public issue; see
  [`SECURITY.md`](SECURITY.md).

## Documentation lifecycle

Start with [the project map](docs/architecture/END_TO_END.md). `README.md` owns
product entry/setup, the map owns task routes, and each domain guide owns its
detailed explanation. Code/configuration define implemented behavior; tests
supply evidence. `AGENTS.md` owns shared coding guidance; local agent files link
there. Personal knowledge remains separate from project documentation.

Before creating a page, search existing routes and topics. Update the owning
section first. A new page must answer a distinct reader question, explain why
an existing section is insufficient, have an incoming link, and identify the
code area whose maintainer is responsible for keeping it accurate. In each PR,
name the affected guide (or explain why none changes) and justify new pages.
Keep initial orientation short: roughly 100 lines for AGENTS and one screen of
routes. These are review signals, not reasons to split coherent explanations.

- **Current:** guides in `docs/architecture/`, `docs/features/`, `docs/guides/`
  and `docs/setup/` explain implemented behavior. Link to code/config authority;
  distinguish release defaults, development configuration and local overrides.
- **Proposed:** use the existing issue backlog for ideas and link related issues
  instead of creating another plan. Create a design only when alternatives need
  sustained explanation. Label it proposed; it is not an implementation promise.
- **Historical:** dated audits, research and `docs/archive/` retain useful
  evidence/rationale. Mark the replacement; do not inject them into normal agent
  orientation as current instructions. Update a shipped proposal's status and
  the current guide rather than maintaining two descriptions.

Keep durable decisions and rejected alternatives in a brief “Decisions and
tradeoffs” section of the owning guide with issue/PR evidence. Create a separate
record only when the decision crosses domains and cannot fit coherently there.
Do not copy constants, inventories or full procedures into multiple guides.
Summarize and link; generated sections are derived views, not editable sources.

Merge overlaps, fix incoming links, then remove redundant text. Git preserves
old content; archive only history with useful unique rationale. Avoid a general
lore folder, a second map, or a separate documentation management service.
Review active-page growth and repeated prose alongside code changes; no model
is required to maintain the documentation structure.

Run `python scripts/dev/check_docs.py` after documentation or agent-registry
changes. It checks Git-tracked non-archive Markdown links/heading anchors and the
agent roster without application imports, dependencies or network access.
`--write-roster` refreshes only the marked block in the existing agent guide;
`--archives` additionally checks historical links. Duplicate prose and page/size
counts are advisory; review growth rather than gaming a line limit. No existing
link debt required a baseline when this check was introduced.

The small `Documentation integrity (offline)` workflow runs on docs-only PRs
without installing the application. A passing link check proves navigation,
not semantic freshness: the code-area reviewer still checks the guide's claims.
