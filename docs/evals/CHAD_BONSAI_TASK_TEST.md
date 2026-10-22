# Bonsai task test — 2026-10-19

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

Prioritize the acting model's task interpretation/execution before further Qwen
review tuning. Independent evidence can already identify a useful failure; Qwen
remains an advisory reviewer. No Qwen inference was run for this test.

## Original-request result

Full dev Brain 8788, normal MCPs and per-turn selection, supervisor
`prism-ml/bonsai-27b`, exact historical executed Meet/Gmail/Prism prompt with the
addressed **Gmail DRAFT ONLY**, ambiguous-recipient clarification and no-unrelated-
changes constraints. The user explicitly instructed proceeding after the earlier
dev restart rejection; the identity-checked dev stop/start then succeeded.
Production 8767 was untouched.

The process-only wrapper changes the dev supervisor, without writing model
preferences. Initial adapter-entry observation confirms Bonsai, 19 bound tools
(seven specialist delegates plus 12 direct tools), including `create_gmail_draft`.
The initial system prompt is byte-for-byte identical to the saved LFM system
prompt (19,996 characters). Initial user/context text contains no Drive document
section, but graph/activity/session/calendar context remains. Calendar/context
timing differs, and this is one stochastic sample, not a controlled model ranking.
The startup log separately confirms an effective `browser_agent` preference pin to
`lfm2.5-24b-a2b`. Thus this is Bonsai supervising the existing specialists, not an
all-Bonsai system. Browser-specialist tool behavior must not be attributed to Bonsai
without that distinction. The original first decision is the Bonsai supervisor's.

The original request returned after 179.0 seconds, paused for clarification:

> What code should be emailed to Jordan?

All three offered options concern source files: all project Python/JavaScript
files, a specific file, or Draftout project source. No option describes the new
Meet's join artifact. Captured reasoning explicitly treats old retrieved coding
session context and the workspace as candidates for the code to email.

Canonical `call-0000` is `ask_user_question`; its result confirms the approval/
input pause. No Meet, Gmail or Prism action was called. `completed=false` and
`awaiting_human=true`. Harness `status=success` means successful capture/pause,
not successful task completion.

**Finding:** the email artifact remains incorrectly framed as source code under
Bonsai. Asking a question alone is not a failure; the source-only choice set and
captured action parameters establish the wrong interpretation here. No unwanted
source file was actually read or sent. These specific Drive hits are not necessary
for this instance of the failure, and removing them/model swapping alone is not a
sufficient repair. Other retrieval, system context and model behavior remain
unisolated contributing hypotheses.

## Clarified continuation and harness finding

The operator supplied one answer grounded in the intended original task:

> The join code/link for the Google Meet you create. Do not email source code or
> source files. Keep the addressed Gmail DRAFT ONLY constraint, and launch the
> Draftout instance using Prism Launcher.

This is a separately recorded clarified continuation, not a pass on the original
wording. Same conversation, one bounded interactive round, 600-second backstop.

The first attempt exposed an eval transport defect: `probe_workflow(resume_token=...)`
sent only `tool_approval_response`. `handle_tool_approval_response()` records the
decision; it explicitly requires the normal follow-up `user_input` to start a turn.
No model turn started, and the inference gate was idle. The idle eval client was
stopped and that attempt retained as `void_harness`, not a Bonsai stall/failure.

`evals/live.py` now sends the answer as `user_input` after resolving the token,
preserving conversation/category/eval platform. A regression verifies message
ordering, token/answer identity, same conversation and completion. No Brain restart
was needed for this client-side change. The corrected continuation started a real
new turn, confirmed in the server log and adapter-entry capture.

### Clarified result

The corrected continuation hit its 600-second wall backstop (602.1s including
client overhead), retaining 25 calls. Server cancellation was confirmed; the
inference gate subsequently reported zero active/waiting requests. This was
ongoing browser work, not an inactivity stall. No full task pass is established.

Within this continuation's own canonical table:

- `call-0000` (`delegate_to_browser_agent`) explicitly requests meeting creation,
  obtaining that meeting's join link/code, and drafting it to Jordan. Thus the
  supervisor preserves the intended dependency after explicit clarification.
  It also assigns the native Prism task to the browser delegate. Verify that
  specialist's effective menu before treating this as a proven wrong-domain cause;
  no Prism action was reached.
- `call-0002` (`browser_type`), `call-0003` and `call-0004` (`browser_snapshot`)
  use `target=textbox` and report that it matches no elements. Later calls use
  concrete references and reach a Gmail conversation; these errors recovered
  enough to permit more browsing, rather than terminating the task.
- The LFM browser path continues recipient lookup: six scrolls, five snapshots,
  three finds, three clicks and other navigation/type/key calls. The supervisor
  never receives a completed delegation result before the backstop. There are
  24 captured tool results and one incomplete parent delegation, with exact
  start-sequence alignment and no unattributed results.
- No captured Meet creation, Gmail draft creation, send or Prism launch. Application
  state was not independently verified. Do not convert absent completion evidence
  into a claim that no side effect whatsoever occurred.

The server log separately records a screenshot invocation error absent from the
WebSocket call table. Preserve this capture limitation rather than assigning it a
fabricated canonical call ID. Three narration-without-action retries are also
visible in the LFM browser log; these are downstream of Bonsai's delegation.

## Next discriminating work

1. Improve the acting supervisor's general artifact/dependency binding, with a
   matched original-prompt check and controls for explicit source-code requests.
   Do not repair Qwen prose to manufacture a better task result.
2. Explicitly choose the browser model for any next execution check. The current
   machine's browser preference remains LFM. A process-only Bonsai browser override
   would distinguish browser-model behavior; a supervisor-only switch cannot.
3. Keep browser scope, native app-launch capabilities and native Gmail/contact
   discovery distinct in the handoff. The selected menu already included draft
   creation. Inspect capability discovery/menu evidence before blaming missing tools.

No supervisor prompt/persona repair, persistent model change or further reviewer
inference was performed. No send action was requested or evidenced. A corrected clarification is a
different input, not proof the original interpretation was fixed.

## Verification and private evidence

32 focused evidence, timeout, resume, completion-check and coalescing tests pass.
The resume fix also has live confirmation that the clarified turn actually starts;
offline success is not task-completion evidence.

Private artifacts under `.agent/experiments/chad-relevance-cutoff/`:
`bonsai-run-01/run.json` (original), `bonsai-clarified-01/` (void harness attempt),
`bonsai-clarified-02/` (corrected continuation), `bonsai-adapter-input.jsonl`
(actual input messages/tool schemas), `runtime-audit.jsonl`, and
`dev-brain-bonsai.*.log`. Existing raw captures and browser snapshots are preserved.

Independent operator summaries: `bonsai-original-adjudication.json` and
`bonsai-clarified-adjudication.json`. Dev remains running with process-only Bonsai
supervisor override; startup SHA `76ef6f35`, boot ID
`6e6cf96c-3cc1-4a1f-ad08-e59cbca67f74`, PID 10608 at final verification. Re-query
before any later process action. Persistent browser preference is still LFM.
