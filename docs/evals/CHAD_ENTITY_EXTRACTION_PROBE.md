# Qwen2.5 entity extraction experiment — 2026-10-19

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Engineering basis and scope

This is a bounded component experiment, not a full Brain behavioral replay or a
production feature. Tool-free extraction uses the already-resident
`qwen2.5-3b-instruct` Q4_K_M instance. No second copy is loaded, no model settings
are changed, and no extracted action is dispatched. Production 8767 is untouched.

The approach uses practices documented and available in 2026, rather than claiming
that an older model has a new published extraction benchmark:

- [Anthropic, Demystifying evals for AI agents, January 9, 2026](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):
  repeated trials, separate transcript/outcome assessment, deterministic checks
  where possible, and explicit harness-failure handling. The local measurements
  below are Chad-specific data; the article does not validate this architecture.
- [LM Studio parallel requests](https://lmstudio.ai/docs/app/advanced/parallel-requests):
  concurrent requests can share a loaded model through continuous batching. The
  installed instance reports `parallel=4`, context length 10,752. Client overlap
  alone does not prove simultaneous GPU execution or lower user-facing latency.
- [LM Studio structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output):
  request a JSON Schema, then independently validate it and its meanings. Syntactic
  conformance does not validate entity types or factual claims.
- [Anthropic, advanced tool use, November 24, 2025](https://www.anthropic.com/engineering/advanced-tool-use):
  tool discovery and compact descriptions/examples inform the design, but this
  experiment does not adopt a second autonomous agent/tool loop.

## Protocol

`evals/chad_entity_extraction_probe.py` contains eight operator-owned cases, two
trials each: original Meet/Jordan/Prism request, NotebookLM alias, explicit source-code
control, local Prism instance, an unfamiliar product-like name, private recipient,
code-only task and Zoom invitation-link dependency. Expected cases are not sent to
the model. The first system prompt has no task-specific Meet/Notebook/Prism examples.

Variant 1 asks for exact entity spans, types, parent links and output dependencies.
Variant 2 is one bounded follow-up simplifying the task to extraction/types only,
with a generic Slack/Firefox example. It does not ask the small model to infer
dependencies. This is prompt development on the same eight cases, not held-out
generalization or a fair comparison of identical contracts. No third variant is
used to chase a pass.

Keep original output unchanged. Reject duplicate JSON keys, schema violations,
invented/case-changed spans and unsupported parent/dependency references. Grade
required names/types and producer links separately from JSON completion. The
grader allows longer spans containing the required name, so additional extraction
and role semantics still need operator review.

Routing is deterministic once a validated extraction exists: people go to local
facts, public-product candidates produce a public-mention-only query, local
instances/files go to local discovery, artifacts to later binding, unknowns to
unresolved evidence. Routes are proposals, not executed tools. Incorrect model
types can still misroute candidates; this is a reason to withhold integration.

The unfamiliar product case requires `unknown` as an abstention control. A
`public_product` output there does not itself prove a factual hallucination or
that the product cannot exist; it fails this stricter uncertainty contract.

## Current baseline and limits

The existing regex classifier misses the original multi-action request and the
`use Prism Launcher...` wording. It catches `go to my gemini notebook` already.
Replacing it wholesale can therefore regress a working alias case.

The initial variant completed all 16 model trials. Fourteen outputs failed exact
dependency evidence validation; two validated but failed the semantic contract.
Thus 0/16 passed the full initial contract. Raw schema output was not empty, but
fabricated/empty dependency spans, product/instance confusion and alias splitting
made it unusable as an authoritative pre-action resolver.

The timing stage of this first runner attempt called an instance method as a class
method and failed before measuring. All 16 trial captures were already saved. This
is an experiment-runner error, not a model timing failure. The method call was
corrected before the extraction-only run.

## Measured extraction-only outcome

Variant 2 completed 16/16 responses; 15/16 passed schema plus exact-span/reference
validation. Only **4/16 passed the complete extraction/type contract**: both trials
of the private-recipient case and both trials of Prism/Cobblemon. No dependency
success is claimed because variant 2 deliberately does not infer dependencies.

Neither variant-2 original-request trial passed. The first changed capitalization
and misclassified Prism Launcher; the second retained supported spans but still
misclassified the product. Capitalization rejection alone is a strict interface
failure, not lack of entity recognition; the routing errors remain independently
visible. NotebookLM alias splitting, source-file/artifact confusion, unnecessary
code-task entities and unfamiliar-name abstention also failed. These are two
trials of eight development cases, not 16 independent representative tasks.

**Decision:** do not integrate this model as an authoritative entity classifier or
router. It can supply candidate names but cannot yet justify trusting its product,
instance and artifact labels. Keep deterministic known-alias matching and local
identity resolution; evaluate hybrid candidate extraction as a separate candidate,
with held-out cases, before changing the live pipeline. No third tuning attempt
or full external-task replay was launched to manufacture a passing result.

## Concurrent-request measurement

Two serial pairs and two concurrent pairs ran in serial/concurrent/concurrent/
serial order, on the same loaded Qwen instance, without tools or action dispatch.
All eight inference responses completed. The extraction calls use variant 2.
Selection calls use the real selector system prompt and its catalog-description
builder against the saved actual 19-tool menu, with simplified user packaging.
They are a latency workload, not a faithful selection-quality eval: they omit the
real shortlist, core-tool/knowledge packaging, sticky tools and full Brain path.

- Serial pairs: 7.855s and 7.379s; median **7.617s**.
- Concurrent pairs: 5.156s and 5.178s; median **5.167s**.
- This small warm sample saves about **32% versus serial extraction plus selection**.
- The selection calls themselves have median **2.695s** across the four pairs.
  Thus adding extraction still costs about **2.47s** versus that selection-only
  reference; it is not free latency. No separate cold selection-only baseline was run.

Requests target the local inference endpoint directly with active-use lifecycle
guards and no load/unload. This isolates endpoint concurrency; it bypasses the
normal gate-routing path and does not prove the same user-visible speedup in Chad.
The actual knowledge-prefetch barrier currently precedes selection, so a production
implementation must reconcile later grounding with the selected menu. Client
start/end overlap and server `parallel=4` support feasibility, but do not expose
GPU scheduling internals. No second model copy is needed for this experiment.

## Recipient check

A separate read-only check extracts the Jordan contact assertion from the exact saved
initial Bonsai graph-context block. One address resolves from that supplied source,
without any public query. This is saved-context resolution, not a live persona or
contacts freshness check and not proof the supervisor will use the result.

The routing unit check separately supplies conflicting synthetic addresses and
requires `ambiguous` with no chosen address. Generic people-name web searches are
never generated. The extraction model sees the original user request, not the
private contact facts. Personal facts are resolved afterward in code.

## Public grounding evidence

Operator web lookup found source evidence that the eventual context needs:

- [Google Meet Space reference](https://developers.google.com/workspace/meet/api/reference/rest/v2/spaces)
  describes `meetingCode` as an output used to join a meeting, and `meetingUri` as
  the joining URL containing that code. This explains the artifact type; the actual
  current meeting code must still come from creating/observing a real meeting.
- [Prism Launcher](https://www.prismlauncher.org/) describes management of multiple
  Minecraft instances. It does not verify that a private instance named Draftout
  exists or establish its actual launch ID.
- [Google NotebookLM](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-discover-sources/)
  documents source discovery and the NotebookLM destination. Resolving the user's
  approximate phrase to that product needs sourced alias evidence, not a claim
  that “Gemini Notebook” was an official historical name.

These were operator-selected public lookups, not an automatic extractor-to-search
integration. They support the content design without certifying the pipeline.

## Reproduce and retain

```powershell
.venv/Scripts/python.exe -m evals.chad_entity_extraction_probe --output .agent/experiments/chad-entity-extraction/new-run.json --simple --repeats 2
```

Requires Qwen already loaded; refuses silent JIT. Model calls have bounded output
and a 90-second request timeout, no output repair or inference retry. Exact requests,
raw responses, usage, timing and residency are private under
`.agent/experiments/chad-entity-extraction/`. `run-01.json` is the original variant;
`run-02-simple.json` is the extraction-only follow-up; `recipient-resolution.json`
contains private contact evidence. Preserve these and do not upload raw content.

Four offline tests cover rejection, semantic grading and recipient routing/conflict
handling. They test the experiment checks, not learned extraction accuracy.
