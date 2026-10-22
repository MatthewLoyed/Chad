# Persona Prompts — How Chad's System Prompts Work

Every system prompt Chad sends to a model is assembled from **persona fragments**:
small markdown files, each with an id and a scope. This doc covers where they
live, how they resolve, how to change one, and how to undo a change.

---

## Vision: pure local-first prompts (isolate prompt errors)

**Prompts are behavior.** They must be:

1. **Local files as the only source of truth** — `prompts/persona/*.md` (baseline) +
   `config/persona/*.md` (overlay). Supabase may *back up* overlays for multi-device
   restore; it is never read on the hot path (`PromptRegistry` / `persona_store`).
2. **Diffable in PRs** — a bad planner doctrine shows up in `git diff`, not in a
   cloud row nobody reviews.
3. **Isolated from orchestration code** — LangGraph **nodes must not own prompt
   prose**. Nodes inject **runtime data only** (user text, `gather_brief`, live
   CONFIRMED TOOL MENU names, failure/feedback). Doctrine, output contracts, and
   scout/planner roles live in `.md` files so a prompt bug is fixed by editing
   markdown and re-running an eval — not by redeploying Python that duplicated
   the same paragraph.
4. **One place per concern** — if `planner.md` and `planner_node.py` both say
   “trust gather_brief,” they will drift. Prefer the file; delete the duplicate
   in code.

Why this matters for debugging: when an eval fails, ask “is this a wiring bug or
a prompt bug?” Wiring lives in `src/`; prompts live in `prompts/persona/`. Mixing
them forces you to chase both on every failure.

`scope: internal` fragments (e.g. `scout_brief_synth.md`) are loaded **by id** for
one-shot jobs (brief compression). They are never injected via
`SystemPromptBuilder.build()`.

---

## Two layers, resolved by fragment id

```
prompts/persona/<id>.md     baseline — generic, git-tracked, ships with the repo
config/persona/<id>.md      overlay  — your persona, gitignored, wins per id
```

The overlay overrides the baseline **per fragment id**. A fragment you have never
customized falls through to the baseline. That is the entire read path — no
database, no network, no cache to invalidate.

| You are | What you get |
|---|---|
| A brand-new user | Baseline only. 24 committed fragments, works immediately, zero setup. |
| A long-time user | Baseline + your overlay. Fragments you (or Chad) have evolved override the generic ones. |
| A Supabase user | Exactly the same. Supabase stores memory/RAG/history — never prompts. |

**There is no local/cloud toggle for prompts, because prompts are always local.**

### Fragment file format

```markdown
---
scope: planner
---

Plan in explicit steps. Prefer the smallest plan that...
```

`scope` decides which prompts the fragment is injected into. An overlay file that
omits `scope` **inherits the baseline's scope** — so you can rewrite a prompt's
prose without accidentally changing where it applies. Valid scopes are listed in
`evals/persona_parity.py` (`KNOWN_SCOPES`); a fragment tagged anything else loads
fine but is never selected, which the parity gate catches.

`always` fragments go into every user- and system-facing prompt. Subagent scopes
additionally receive `subagent_base`.

---

## Changing a prompt

**By hand** — edit or create the overlay file:

```bash
# Customize the planner prompt without touching the shipped baseline
cp prompts/persona/planner.md config/persona/planner.md
```

Then edit `config/persona/planner.md` and restart the Brain.

**Programmatically** — `MemoryRepository.update_persona_config(id, content)`, which
is what the reflection loop, the `manage_persona_rules` tool, and the GUI persona
editor all call. Writes always land in the overlay; the baseline is never modified
at runtime, because it is the generic persona published to other people.

Every write records a version in the local `persona_history` table **before**
touching the file:

```python
from src.infrastructure.repositories import persona_store

persona_store.history("tools")        # newest-first version list
persona_store.rollback("tools", 8)    # restore version 8
```

Rollback is recorded as a *new* version rather than rewinding the log, so the fact
that a rollback happened stays visible. This matters because reflection lessons
write unattended — a bad auto-write needs a way back that doesn't involve
reconstructing prose from memory.

---

## Why files instead of a database

Prompts change more often than anything else in an LLM stack, so they need to be
diffable, reviewable in a PR, and revertable with the same tools as code. Rows in a
database are none of those things.

Two concrete failures drove the move (issue #589):

1. **The Chad-on-Chad dev loop (#358) is impossible otherwise.** That loop has the
   coder fix a prompt and an eval measure the improvement. If the prompt under test
   and the prompt in production are different artifacts, the loop reports "no
   improvement" forever and the model gets blamed. Now the coder edits
   `config/persona/planner.md` and the eval loads that exact file.

2. **Cloud-only prompts fail silently.** The live `user` fragment had been blanked
   to a single space by an unattended write. Nothing caught it, because nothing
   about a database row is visible in a diff.

---

## The `user` fragment is deprecated

`user` used to hold personal facts (name, timezone, style). Those moved to the
knowledge graph — `config/personal_knowledge.md`, compiled to
`data/knowledge_graph.json`, injected per turn by `memory_engine`. `reflection.py`'s
`GRAPH_SCOPES` already routes every `user`-scope lesson there.

The fragment is listed in `persona_store.DEPRECATED_IDS` and is skipped by both
layers. To change what Chad knows about you, edit `config/personal_knowledge.md` —
see [MEMORY_AND_CONTEXT.md](MEMORY_AND_CONTEXT.md).

---

## The parity gate

```bash
python evals/runner.py --persona-parity
```

Free and deterministic. It does **not** demand the two layers match — the overlay
is supposed to diverge, and on a maintainer's machine it holds personal content
that must never be committed.

It fails on the divergence that actually hurts people: a fragment that exists
**only** in an overlay. That developer's Chad works while every fresh clone
silently boots without that prompt. It also flags empty fragments, unknown scopes,
and files resurrecting a deprecated id.

---

## Worktrees

`prompts/persona/` is git-tracked, so every worktree gets the baseline for free.
`config/persona/` is gitignored and does not, so it is **copied** from the main
checkout when a worktree is created:

- ad-hoc Claude Code worktrees — `~/.claude/hooks/link-worktree-secrets.js` (`COPY_DIRS`)
- the official dev worktree — `scripts/prod/setup_dev_worktree.ps1`

**Copied, not hardlinked — unlike `.env` and `secrets.yaml`.** Those are read-only
credentials, so one shared inode is exactly what you want. The persona overlay is
the opposite: it is actively written by the reflection loop, the persona editor,
and prompt experiments. A hardlinked write lands on the same inode, so a throwaway
worktree would silently rewrite prod's real persona — and prompt-editing
experiments (Chad-on-Chad, #358) do that by design, on every run.

Copying gives each worktree a realistic starting persona that then diverges
harmlessly. The tradeoff is that a worktree's copy does **not** track later changes
to prod's persona; delete `config/persona/` in the worktree and re-run the setup to
re-sync.

---

## Migrating from Supabase-backed prompts

Installs predating #589 kept the live persona in Supabase's `persona_config` table:

```bash
python scripts/setup/migrate_persona_to_local.py --dry-run
```

Drop `--dry-run` to write. It copies the latest active version of every fragment
into `config/persona/*.md`. Supabase rows are left untouched as a backup, so
reverting means deleting the overlay files. Deprecated and empty fragments are
skipped rather than migrated.

---

## Files at a glance

| File | Role |
|---|---|
| `prompts/persona/*.md` | Generic baseline — **committed**, safe to publish |
| `prompts/persona/planner.md` / `scout.md` | Plan-lane doctrine (nodes must not duplicate this) |
| `config/persona/*.md` | Your overlay — **gitignored**, may be personal |
| `src/infrastructure/repositories/persona_store.py` | Resolution, versioned writes, rollback |
| `src/infrastructure/repositories/prompt_registry.py` | In-memory cache warmed from the store |
| `src/core/intelligence/prompt_builder.py` | Assembles fragments into a scope's system prompt |
| `src/core/orchestration/nodes/planner_node.py` / `scout_node.py` | Runtime injects only (brief, menu, user text) |
| `scripts/setup/migrate_persona_to_local.py` | One-time Supabase → local migration |
| `evals/persona_parity.py` | The parity gate |

### Why Scout briefs are raw last messages

Scout runs only when the planner dispatches it (`dispatch_scout`). When Scout is
done, its **final tool-free message is `gather_brief` verbatim** — no separate
synth pass, no usability filter, no tool-result dump. If Scout never wrote a
text report (stopped after tools only), the node asks once for a report turn,
then captures that message as-is.
