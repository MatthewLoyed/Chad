# Concrete finding: Meet join code became source code

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Observation and impact

Original request: "start a google meet and email jordan the code, and use prism
launcher to launch draftout instance". The intended dependency is:
create Meet → obtain that meeting's join code → draft email containing that code.
Launching the named Prism instance is a separate requested action.

The full dev-8788 capture demonstrates an earlier interpretation error, before the
missing routines and browser kill-switch failures:

- The initial recorded reasoning explicitly asks whether "the code" is a file or
  script, mentions unrelated retrieved documents, and proposes finding code files.
- `[call-0000]` (`delegate_to_traversal_agent`) requests reading two unrelated Drive
  documents and extracting "code snippets, project names, or technical details that
  might be 'the code'" to put in the email. The document titles/IDs are retained only
  in private local evidence.
- `[call-0109]` (`delegate_to_code_agent`) eventually requests reading a local Python
  source file and putting its full contents into the Gmail draft. That is not the
  meeting code requested by the user.

This substitution is an observed behavioral error, independently of whether the
external task could otherwise have completed. It sends the task toward unrelated
document retrieval and a wrong potential email payload. No successful Gmail action
was recorded; an actual wrong email being delivered is not established.

## What the evaluator did and missed

The first completed Qwen review mentioned misinterpretation, but described "wedding
code", a phrase absent from the request but present in Chad's drifted Drive-search
queries. Qwen adopted execution wording instead of preserving the original task's
meaning. The later canonical-ID review concentrated
on tool failures and omitted this upstream substitution. Codex identified the exact
referent error by inspecting the original first reasoning and handoff, not by treating
Qwen's hypothesis prose as verified diagnosis.

The budgeted evaluator view does not include raw internal reasoning, and truncates
long delegation parameters. The first delegation names documents before explaining
the wrong code-snippet objective; clipping can therefore hide the decisive intent.
This is a limitation of evaluator evidence, not proof that the model would diagnose
the issue correctly with more text. Future diagnosis of intent should retain the
semantic objective of each handoff, with omitted material explicitly indicated.

## Evidence about contributing causes

Read-only inspection located the actual initial LFM outbound messages at 12:09:03
in the local prompt dump. It contains exactly two messages: a 19,996-character system
message and a 3,036-character user message. The current task is present at offset 0.
Graph, activity, Drive, prior-session and calendar context follow it. The Drive block
includes the documents the model immediately selected for its first delegation.
The block explicitly says these are optional and to ignore unrelated hits.

This supports a **retrieval-distraction hypothesis**: matching the recipient's name
made unrelated documents salient, and the model replaced the artifact meaning with
available file content. The model itself refers to those documents while deciding
what "code" means. Causation is not established by this single run. This was a fresh
execution conversation; old session material was retrieved context, not an earlier
active chat turn. The prompt was not missing the original request.

Relevant existing code and policy:

- `format_knowledge_boost()` in `src/core/orchestration/nodes/graphrag_node.py`
  already labels retrieval optional. Repeating "ignore unrelated hits" alone adds
  little new behavior.
- `supervisor_node.py` already reinjects the overall objective when both an active
  plan and `plan_goal` exist. This was a plain agent run without an active plan.
  Reuse that anchoring mechanism if changing runtime context; do not build a second
  planner. Repetition alone is not proven sufficient: recorded reasoning rereads
  the original task and still chooses source files.
- `prompts/persona/tool_calling.md` already says dependent calls go sequentially.
  The missing interpretation here is which artifact the dependent email consumes.
  Active ignored persona overlays may shadow templates; inspect/capture effective
  sources before any prompt A/B. Do not blindly patch only a tracked template.

## Smallest candidate fix and verification

First test a general dependency/artifact-binding instruction in the effective
supervisor prompt, before choosing or delegating executable actions:

> When a request creates or obtains an artifact and then asks to send/use its code,
> link, result or contents, bind the later action to the output of that requested
> action. Preserve that dependency in delegated tasks. Retrieved names, files and
> prior sessions may supply needed facts, but must not substitute a different
> artifact or change the requested payload. Ask only when the current request
> leaves the referent genuinely unresolved.

For this task the expected interpretation is "email the newly created Meet's join
code/link", not existing source code. Recipient grounding can use contact information
without opening unrelated file bodies. The initial delegated task should create the
Meet and return its real join artifact, while Prism remains an independent branch.

Before treating this candidate as a fix, use a bounded full-dev-8788 interpretation
check with effective persona capture, and controls:

1. Original request with the same distracting retrieval: bind email to Meet output.
2. Original request with unrelated retrieval omitted: compare interpretation, not
   successful tool execution. This discriminates retrieval influence from baseline
   language understanding.
3. Explicit "email Jordan the source code from the named file; also start a Meet":
   preserve the source-file payload, preventing a Meet-specific keyword shortcut.
4. Another producer/consumer task, such as creating an invitation and emailing its
   link: preserve the generated artifact dependency.

Hold executor/model settings fixed; change one prompt/context factor at a time.
An addressed-draft or plan-only check suffices before replaying real external actions.
If prompt binding fails, inspect retrieval relevance and task anchoring next; do not
disable personal context broadly, add a regex that rewrites "code", weaken browser
guards, or increase timeouts to hide wrong-goal execution.

These are proposed controls, not completed A/B evidence. No product prompts, active
overlays, production services or model roles were changed by this investigation.
The bug is ready to triage without a proven repair or fully proven causal mechanism.

Follow-up: a candidate relevance cutoff was subsequently tested and did not resolve
the confusion. A separate reasoning-guard defect was found and fixed. See
[cutoff experiment and guard evidence](CHAD_RELEVANCE_CUTOFF_TEST.md).

Private sources: `corrected-run-01/run.json`, `initial-supervisor-messages.json`,
and both Qwen review captures under
`.agent/experiments/chad-meet-prism-self-diagnosis/`.
