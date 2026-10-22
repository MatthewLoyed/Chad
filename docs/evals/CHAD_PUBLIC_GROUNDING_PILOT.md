# Public-reference grounding pilot — 2026-10-19

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Scope and authorization

The user authorized proceeding after an automatic tool approval rejected the
initial preparation/restart command. Read-only runtime inspection and a later
narrowly scoped test launch succeeded. No dev restart, model preference change,
persona repair or production-8767 action occurred in this pilot.

The current full dev Brain 8788 uses LFM2.5-24B-A2B, not the earlier process-only
Bonsai override. Both arms use this same running process and its normal selector,
tools/MCPs and retrieval. Historical PID 4552 / boot ID
`2952e398-7814-4d09-addf-4077883ff705`, startup SHA `76ef6f35`; re-query before action.

This is an initial first-decision experiment, not automatic entity extraction or
end-to-end task completion. The operator selected the public entities. A short
reference block is appended as data to the user-message envelope in the grounded
arm. The original task and addressed Gmail DRAFT ONLY constraints remain the
prefix, but the total submitted text differs. No clarification telling the model
which meaning of code to choose was added.

## Reference facts

Fresh read-only source inspection supplied two short paraphrases:

- [Google Meet Space reference](https://developers.google.com/workspace/meet/api/reference/rest/v2/spaces):
  a meeting space provides a joining code and a joining URI containing that code.
  The card contains no real meeting code and does not establish tool availability.
- [Prism Launcher](https://www.prismlauncher.org/): the product manages Minecraft
  instances with their own settings, mods and configurations. It does not establish
  that Draftout exists locally or invent an instance ID.

Raw submitted cards and exact request strings are retained in the private capture.
The model is not instructed to send meeting code, avoid source code, or choose a
particular tool. Source-code wording is tested separately as a control.

## Baseline and grounded arm

Baseline, no extra references: first decision after about 58 seconds is two calls:
`web_search` for `prism launcher draftout` / `draftout drone software`, and
`search_for_tool(query="list google drive files code", category="workspace")`.
This evidences continued code-file discovery and a questionable public-product
interpretation. No Meet-code binding or task completion is established. The
WebSocket disconnected at the first decision and server cancellation was confirmed.
Calls may have begun before cancellation; do not claim no side effects whatsoever.

Grounded arm: actual initial LFM input includes both reference facts. The system
prompt SHA-256 is identical to baseline. It ends after about 101 seconds, without
tools, with a generic generation-failure response. The server log independently
shows a narration-without-action retry followed by the 3,000-estimated-token
reasoning guard during the retry. Do not infer a context-length failure merely
from the generic error text.

Captured reasoning initially describes obtaining a meetingCode/URI, later returns
to project/wedding code, and considers unsupported recipient addresses. Those are
internal hypotheses, not external actions or actual addressed drafts. The facts
were present and used in portions of reasoning, but no stable behavioral success
was demonstrated. The turn reached a terminal response before disconnect; no
active turn remained to cancel.

## Explicit source-code control

A third fresh conversation requests the source code from `report.py`, plus a Meet,
with the same draft-only constraints and both product cards. Its first decision
after about 49 seconds is `run_saved_routine(routine_name="code_agent")`. This
does not establish that such a saved routine exists, and no result is captured.
Server cancellation was confirmed. The initial recorded interpretation preserves
the explicit source-file objective rather than substituting a meeting code. That
is a narrow referent control, not successful file discovery, valid delegation or
task completion. The control's actual system hash matches the other arms.

## Important retrieval confound

This is **not a clean causal A/B**. Both calls invoke normal retrieval using their
whole submitted input. Appending reference text therefore changes the retrieval
query, not just what the supervisor knows:

- Baseline appended retrieval: 2,256 characters; Jordan's known email is present.
- Grounded appended retrieval: 3,071 characters; that email is absent. A further
  `GROUNDED WEB SERVICES & APPS` section appears in addition to graph/activity/
  session/calendar context. Neither arm contains a Drive document section.

This explains why the grounded reasoning's missing-email concern cannot be
assigned solely to model failure to read a fact: the actual relevant address was
not in that arm's input. It does not explain the original baseline's failure to
use a supplied address. Keep the two observations separate.

The pilot identifies a concrete integration requirement: derive retrieval queries
from the original task before adding reference text, and keep personal identity
facts independent of public-product grounding. Otherwise an attempted grounding
improvement can displace useful personal facts and trigger further entity lookups.
Freeze personal context and the bound menu for an isolated model replay; separately
test the real pipeline where selection is allowed to change. No fix to that
integration was implemented or validated in this pilot.

## Evidence integrity

Private directory: `.agent/experiments/chad-grounding-ab/`. The runner checks dev
port plus server/auth/disk preflight, uses fresh conversation IDs, stops on first
captured tool decision or terminal response, retains partial evidence, and checks
server disconnect cancellation before subsequent nonterminal trials.

`audit.py` selects the initial LFM message pair by exact submitted prompt, model
and the prompt-dump byte offset captured immediately before the trial. This avoids
reusing an earlier time-only-header match. It records full private messages and
the system hash. There is no request correlation ID inside the prompt dump, and
downstream adapter transformations remain a stated limit.

Files: `baseline-01.json`, `grounded-01.json`, their `*-input.json` snapshots,
and the separately labeled `source-control.json`. Raw personal context and model
reasoning are private and must not be copied into public reports.

## Decision

Do not claim that adding public facts fixed Chad. This pilot shows the facts reached
the model but produced no action success, and exposed a query-contamination confound.
It does not disprove the value of grounding. The next discriminating experiment
must hold the original task retrieval inputs fixed while adding sourced reference
data after retrieval. Do not compensate by merely adding more prompt instructions,
running an unbounded model comparison or expanding inference budgets.
