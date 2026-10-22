# Scout discovery/report contract fixes and live verification — 2026-10-19

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Failure and repairs

The prior captured 21:16:58 Scout history contained 22 calls, including 15 rejected
finish_scout_report attempts. A system-category tool search returned no permitted
match even though check_app_exists was registered under os. Scout then used nine
web queries to investigate local state. The report contract required source_call_id,
but its schema exposed arbitrary string dictionaries rather than named fields.

The report schema now has explicit question/status/finding/source/source_call_id
properties, a status enum and required fields, with no unresolved JSON-schema refs.
The canonical schema is pinned through the real ChatChad schema conversion path.
Returned observations include _scout_source with the reference to copy. Errors name
the failing item/field and enumerate actual returned references. Two invalid report
attempts terminate with an explicit report_failed record; they cannot consume the
entire research budget in a retry loop.

Scout discovery first resolves exact advertised tool names (including space-to-
underscore normalization), independent of a mistaken category. A scoped search
without permitted matches gets one retry without category. Only approved read-only
schemas bind. Discovery refreshes LOADED TOOLS in Scout's next system prompt and
returns loaded_tools_now. A short worked example demonstrates public lookup,
personal memory, app discovery/inspection and sourced reporting. Planner guidance
asks for execution-relevant URLs rather than installed website apps or speculative
UI layouts. Scout guidance distinguishes an actual answer from tutorial titles.

## Live tests

Both runs used the user's long Meet/Jordan/Prism prompt unchanged, the full dev Brain
on 8788, actual tools and memory, and Bonsai inherited by Scout. They ran in the
planning lane and stopped at awaiting_approval. No approval was submitted. No
meeting was created, email drafted/sent or Prism instance launched. Dev Hands
handled actual read-only app inspection. Production 8767 was not restarted.

Run 1: 115.3 seconds end to end; one web-search batch, one memory lookup, one tool
discovery and one check_app_exists, then one accepted report. The app tool was
present in the next model request after discovery and executed successfully through
Hands. Four findings were returned, one unresolved, with zero report rejections.
An item labeling tutorial titles as a verified how-to answer remained weak, so the
prompt was tightened and the same user prompt was run again. This first run used a
non-UUID conversation identifier; remote conversation persistence fell back to local
SQLite. Run 2 used a valid UUID.

Run 2: 107.9 seconds end to end; one memory lookup, one web-search batch and one
accepted report, with zero report rejections. The planner requested only the email
and official starting URL; both were reported with references. The recipient value
matched the actual saved memory. The returned https://google.meet/ appeared in the
search result and independently redirected to Google's official product page
(https://workspace.google.com/products/meet/). This is an official entry link, not
proof of signed-in browser state or an already-started meeting. Google's meeting
instructions are at https://support.google.com/meet/answer/9302870?hl=en.

Both runs completed required coverage review and proposed four-step plans before
pausing. These are planning/Scout successes, not full-task execution passes or proof
of general model reliability. The original captured history is an observational
comparison, not a controlled statistically significant benchmark.

## Evidence and validation

Raw contact-bearing traces, adapter inputs, live preflight records and source hashes
remain ignored under .agent/experiments/scout-contract-live/ (run1/run2, adapter1/
adapter2 and summary1/summary2). They show actual outbound schemas and model context.
The dev test process used a process-only Bonsai override and observational adapter
logging; persistent model preferences were not changed. Test-owned Brain/Hands
processes are stopped after capture so their override cannot interfere with GUI
model switching.

77 focused backend tests pass, including exact-name/category recovery, loaded-menu
refresh, actual outgoing schema fields, observation references, report retry bounds,
checkpoint/handoff behavior, app inspection and planner gates. git diff --check
passes. No broader tool-selection benchmark or evaluator-model swap was needed.


## Plan specificity follow-up

Planner baseline and effective local overlay now use one concise contract: identify
named entities and required concrete execution identifiers before gathering; compare
Scout coverage with the original request; use the available targeted follow-up for
missing URLs/addresses/paths. Related actions for the same tool/specialist form one
end-to-end outcome, with explicit dependency exceptions rather than global merging.
Removed artificial fixed step counts and conflicting blanket delegate-only prose.
Examples contrast split browser microsteps with one full meeting/code delegation,
then a dependent addressed draft. Future meeting codes remain runtime outputs, not
invented planning facts. Scout also flags obvious missing entity identifiers.
No live replay was performed for this prompt-only follow-up.
