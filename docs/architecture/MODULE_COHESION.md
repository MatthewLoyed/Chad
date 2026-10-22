# Module cohesion

How this repo decides a module is too big, how that's enforced, and what's still
outstanding.

## Why line limits were dropped

The old rule was "modules under ~800 lines, hard ceiling 1000." Three files killed it:

| file | size | shape | actually healthy? |
|---|---|---|---|
| `src/remote/endpoints.py` | 1143 | 42 independent route handlers | **yes** |
| `src/core/orchestration/turn_coordinator.py` | 1204 | one class, one 448-line method | no |
| `src/application/facades/assistant/config.py` | 1078 | 43 flat config accessors | **yes** |

The rule flagged all three identically. So it pushed toward splitting the two healthy
ones — which would add imports and indirection while reducing nothing — and said
nothing about *why* the middle one hurt. A rule that's wrong that often gets ignored,
and it was.

## The rule now

> How much you must hold in your head to change one thing safely — not line count.

A module should be describable in one sentence without an "and". Split when it has
more than one reason to change, when its parts share mutable state you have to scroll
to keep straight, or when a test needs heavy mock scaffolding to reach one behavior.

**Long-but-flat is fine.** In a route table, registry, formatter map, or an adapter
mirroring someone else's API surface, length is additive, not compounding. Adding a
43rd route doesn't make the other 42 harder to understand.

## Enforcement

`tests/unit/test_module_cohesion.py` measures two things per file:

- **largest class** — over 400 lines. A class is shared mutable state; every method can
  touch every field, so its size is the size of what you must reason about.
- **longest function** — over 120 lines. A linear span you must read start to finish.

It never measures file length. That is what lets `endpoints.py` pass while a god class
fails — the distinction the old rule couldn't make.

Existing debt is frozen in `tests/unit/module_cohesion_baseline.json`. The suite fails
on a **new** violation or an **existing one getting worse**. It never fails for
improvement.

```bash
python scripts/dev/regen_cohesion_baseline.py
```

Use that to lock in a shrink. It prints `resolved` / `improved` / `ADDED` / `GREW` per
entry, so re-baselining to hide growth is visible in review rather than buried in a
JSON diff. It cannot stop you — it makes the decision conscious.

### Upkeep routine

**Approved Windows alpha exception — October 22, 2026.** The maintainer
explicitly accepted the existing code organization for this bounded alpha,
rather than expanding release preparation into a refactor of 54 modules.
The baseline records the current 124 oversized entries, including 30 new
entries and 52 growth violations identified in the private release audit.
This is an accepted maintenance debt exception, not a claim that those modules
are all healthy. The 400/120 thresholds and both ratchet tests are unchanged;
future growth must still be fixed. Original baseline history remains in Git.

- **When the guard fails:** split the code. Re-baselining is the exception, and the
  commit message must say why.
- **After shrinking anything:** re-run the regen script, so the ground is held.
- **The baseline should trend down.** 95 entries at introduction (2026-07-27), 94 after
  the turn-pipeline work. If it only ever grows, the ratchet has become a rubber stamp.
- **Don't tune the thresholds to fit the code.** 400/120 were picked from the real
  distribution; raising them to shorten the baseline defeats the point. The baseline
  lives in a data file precisely so the limits never have to be negotiated down to keep
  the test file readable.

## Worked example: `turn_coordinator`

The reference case for what "split" should look like.

| | before | after |
|---|---|---|
| `TurnCoordinator` class | 931 | 354 |
| `turn_coordinator.py` | 1204 | 419 |
| longest function | 448 | 78 |

Order mattered. The 448-line `_execute_turn_internal_wrapped` could not be extracted
cleanly while per-turn state (`last_turn_metrics`, `_last_tool_selection`,
`_current_turn_mode`) lived on the process-wide shared coordinator — pulling the
pipeline out first would have shuffled mutable state around without reducing it. So:

1. Extract collaborators with narrow interfaces (`status_emitter`, `model_preloader`,
   `tool_preparation`, `status_labels`).
2. Move per-turn state into a ContextVar scratch (`turn/scratch.py`). This also fixed a
   real bug: concurrent turns were overwriting each other's metrics.
3. *Then* extract the pipeline (`turn/pipeline.py`), which now has a real owner with a
   bounded lifetime.

**What was deliberately not done:** splitting the pipeline into phase methods over a
shared mutable dataclass. That relocates shared state instead of reducing it and costs
the linear read. `TurnPipeline` is 675 lines and knowingly over the class limit —
every remaining phase needs 8–14 of the turn's fields, so a further cut degenerates
into passing a parameter bag between halves of one process. The per-class line count is
a coarser proxy than the principle in this case; what actually improved is that no
phase method needs more than a handful of fields.

## Targets at risk

Triaged by shape, not size. Sizes from the baseline; branch counts from `ast`.

### Genuine god functions — highest value

| target | size | tell |
|---|---|---|
| `gui_limb.py:GUIChadLimb.handle_message` | 447 lines, **67 if-branches** | A message-type dispatch written as one function. The fix is a handler registry keyed by message type — which then becomes legitimately long-but-flat, like `endpoints.py`. Clearest win on this list. |
| `openai_adapter.py:OpenAIAdapter.generate` | 579 lines, **68 if-branches** | The class (1073) is an adapter mirroring an external API, so its *length* is defensible; a 579-line method is not. Same shape in `litellm_adapter._generate_impl` (396). |
| `supervisor_node.py:call_supervisor` | 505 lines, 39 branches | Plus `make_supervisor_node` (510) — a factory returning a closure, so the two nest and inflate together. Needs the closure's state made explicit first, same ordering lesson as `turn_coordinator`. |

### Genuine god classes

`UIStateSyncer` (909) · `ConversationOrchestrator` (878) · `MainWindow` (836) ·
`SessionManager` (632) · `GraphRAGClient` (859)

Real shared-mutable-state classes. `ConversationOrchestrator` is the natural follow-on
to the turn work — it sits directly above `TurnCoordinator`.

### Probably fine — do not "fix"

- **`config.py:ConfigFacadeMixin` (1072)** — 43 methods, largest 82. This is flat.
  It trips the class threshold, but splitting it would be exactly the busywork the old
  rule used to demand. Left in the baseline as debt-shaped-like-debt; treat as low
  priority and consider excusing it explicitly if it comes up again.
- **`office/validators/base.py:BaseSchemaValidator` (832)** — 20 methods, largest 105.
  Mostly flat, not urgent.
- **`turn/pipeline.py:TurnPipeline` (675)** — see the worked example above.

The general lesson: **check the shape before you split.** Method count and branch count
tell you whether a big number is a route table or a god object, and the answer changes
what you should do.

## See also

- `CLAUDE.md.example` → Conventions (the short form of the rule)
- `CONTRIBUTING.md` → Pull requests
- `.claude/skills/code-reviewer/SKILL.md` → applies the same test in automated review
