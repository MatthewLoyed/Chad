# GUI trace harness repairs — October 19, 2026

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

Follow-up to `CHAD_REFINED_GUI_TRACE_TRIAGE.md`. Implemented in the working tree;
validated offline. No live task replay or production restart was performed.

## Changes

- **MCP snapshots:** dictionary text blocks now enter the SDK-object hydration and
  pruning path. File snapshots yield inline content and Set-of-Mark refs. Unreadable
  files give an explicit incomplete-observation warning. Hydration stays within
  the project directory; replacement preserves literal backslashes in page text.
- **Visible text:** pruning preserves text-valued generic leaves, including room
  codes and share links, while continuing to drop empty layout containers.
- **Screenshot context:** the existing graph text-result channel replaces raw image
  data with an explicit artifact/fallback notice before truncation. Screenshot paths
  remain. This prevents clipped base64 and misleading image-reading assumptions;
  it does **not** add native vision support to LFM.
- **Plan entry:** proposed steps always start pending at both the tool boundary
  and planner normalization. New plans cannot mark work complete or introduce an
  unrecognized `in_progress` alias. Runtime owns subsequent status changes.
- **Budget handoff:** browser exhaustion crosses specialist/parent boundaries and
  blocks browser re-entry before model swap or child invocation. Local specialist
  caps remain distinct. Partial summaries preserve progress with failure signatures
  and no longer count as successful delegate completions. Desktop work remains callable.
- **Fresh delegation:** local step counters/cap flags reset for a new bounded
  delegation. The shared conversation browser budget remains enforced.
- **Observation feedback:** successful snapshots/relative scrolls no longer receive
  the blanket assertion that identical arguments mean old results should be reused.
  Other loop detection and the browser budget remain.

## Verification

**111 tests passed** across new boundary regressions and adjacent MCP, specialist
approval, subagent, planning, workflow grounding and repetition tests. The new tests
exercise the actual MCP producer shape, a compiled graph tool node, both callable
and graph-node handoffs, terminal browser re-entry, local caps and desktop isolation.

Offline checking of the saved successful meeting snapshot also passed: the ready
dialog, room code, share link and leave-call control survive pruning (155 to 99
lines). Private result:
`.agent/experiments/chad-refined-gui-trace/postrepair-pruning.json`.

`git diff --check` passed. Private browser artifacts remain untracked/ignored.

## Remaining work

These repairs do not establish that the acting model retains meeting success in
its summary, chooses the correct draft tool, resolves Jordan's stored email, or
finishes Prism. Those are the next behavioral/selection/retrieval checks.
Depth-limited snapshot coverage and structured created-artifact handoff remain
follow-up work. Do not raise budgets to mask failures.

Next behavioral verification: full dev Brain `:8788`, effective supervisor **and
specialist** model IDs, exact prompt provenance. Never restart production `:8767`.
