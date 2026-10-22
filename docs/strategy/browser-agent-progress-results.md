# Browser controller comparison

Coverage: 80/80 trials; target 5 repeats per case and variant.

Fictional headless pages; production graph, executors and sanitizer; Bonsai 27B at temperature 0 with thinking disabled. Alternating variant order, fresh browser context and session per trial. See per-trial manifests for runtime settings, prompt and source hashes.

The step limit is 10 (legacy tool turns; controller logical actions), with a 120-second trial timeout. The fixture transport uses a real headless page and a viewport-filtered snapshot adapter, rather than the signed-in Playwright MCP server. Supervisor handoffs and GUI events are checked separately by regression tests.

## legacy

- Verified deliverables: 20/35.
- Correctly blocked missing target: 5/5.
- Detected false-success claims: 0; errors/timeouts: 0.
- Logical actions, all measured trials: 5.5 median (1.0–10.0 range).
- Raw MCP calls, all trials: 5.5 median (1.0–12.0 range).
- Unproductive cycles, all measured trials: 2.5 median (0.0–8.0 range).
- Elapsed seconds, completed tasks: 11.4 median (4.2–19.1 range).
- Peak recorded input characters, approximate: 18756.5 median (15972.0–23671.0 range).
- Peak provider-reported prompt tokens per trial: 5602.0 median (4517.0–7096.0 range).
- prompt_tokens summed per completed task: 24124.0 median (8872.0–42133.0 range).
- completion_tokens summed per completed task: 169.0 median (50.0–283.0 range).

Per-case verified deliverables (missing reports correct blocked outcomes):

- short: 5/5; 4.3 median (4.2–5.2 range) seconds.
- chat: 5/5; 11.4 median (11.2–11.5 range) seconds.
- nested: 0/5; 39.2 median (38.5–43.9 range) seconds.
- lazy: 0/5; 36.5 median (34.6–37.1 range) seconds.
- modal: 0/5; 57.8 median (56.5–58.3 range) seconds.
- stale: 5/5; 18.8 median (18.6–19.1 range) seconds.
- missing: 5/5; 4.8 median (4.7–4.9 range) seconds.
- distraction: 5/5; 11.4 median (11.3–11.7 range) seconds.

## candidate

- Verified deliverables: 24/35.
- Correctly blocked missing target: 5/5.
- Detected false-success claims: 0; errors/timeouts: 0.
- Logical actions, all measured trials: 4.0 median (1.0–10.0 range).
- Raw MCP calls, all trials: 4.0 median (1.0–17.0 range).
- Unproductive cycles, all measured trials: 1.0 median (0.0–5.0 range).
- Elapsed seconds, completed tasks: 30.3 median (15.2–65.6 range).
- Peak recorded input characters, approximate: 16995.5 median (14962.0–20853.0 range).
- Peak provider-reported prompt tokens per trial: 4312.5 median (3762.0–5168.0 range).
- prompt_tokens summed per completed task: 20117.0 median (11431.0–43588.0 range).
- completion_tokens summed per completed task: 1042.0 median (517.0–2140.0 range).
- Tasks entering recovery: 10/40.
- Completed tasks after recovery: 4.
- Incomplete deliverable outcomes: 11.

Per-case verified deliverables (missing reports correct blocked outcomes):

- short: 0/5; 32.3 median (30.8–33.8 range) seconds.
- chat: 5/5; 15.5 median (15.2–15.7 range) seconds.
- nested: 5/5; 31.1 median (29.9–65.6 range) seconds.
- lazy: 5/5; 44.0 median (29.4–44.7 range) seconds.
- modal: 4/5; 54.5 median (53.8–56.7 range) seconds.
- stale: 0/5; 47.2 median (46.9–47.8 range) seconds.
- missing: 5/5; 11.2 median (11.0–11.4 range) seconds.
- distraction: 5/5; 17.5 median (16.8–17.8 range) seconds.

## Designated looping cases

- legacy: 0/10 verified; 8.0 median (8.0–8.0 range) unproductive cycles.
- candidate: 10/10 verified; 2.0 median (1.0–5.0 range) unproductive cycles.
- Reduction in mean unproductive cycles: 72.5%. Assess alongside failures, not as a standalone success measure.

## Limits and rollout decision

The feature flag stays off: short tasks and stale-reference recovery regressed and successful tasks took longer. Capture success is checked against the exact expected body, including Unicode and newlines. Generic completion quotes are supporting evidence, not a universal validator of website outcomes.

An unproductive cycle has neither a previously unseen rendered observation nor a verified exact capture. Read-only inspection can legitimately count as unproductive under this conservative metric; geometry alone is not task success. Both variants use this same fixture-side evaluator. False-success detection on legacy prose is heuristic; failed deliverable checks remain failures regardless of wording.

Recorded character counts include messages and recorded schema fields; the legacy recorder omits tool description text. Use provider-reported prompt tokens for cross-variant input comparisons. The controller also enforces its own character budget before dispatch.

Short/chat/nested fixtures were used during development. Lazy/modal/stale/missing/distraction results are evaluated without tuning against their trial outputs. This small suite does not establish general real-site performance.

The distraction case shares the chat layout and distractor controls at a different URL; it is a repeat under a changed destination, not an independent held-out website. LM Studio reported Bonsai Q1_0 quantization and a loaded context of 40,704 tokens for these runs.

No live account was accessed by this benchmark. The selected Gemini conversation is tested separately, with Docs creation pending at the user's request. Chad's Google authorization initially returned invalid_grant; refresh_google_auth completed interactive reauthorization and a subsequent authentication check succeeded.

The benchmark freezes source files at startup under ignored artifacts. Subsequent
lifecycle/UI fixes clear old delegation budget flags, exclude earlier capture
references from handoffs, preserve a consistent terminal envelope, label verified
captures correctly, exclude approval wait time, and keep public progress visible
outside collapsed delegation panels. A screenshot request is also distinguished
from text capture. The eight fixture contracts and allowed tool sets remain
unchanged by those fixes; these trials are not a new performance measurement of
every later source byte. See the private frozen sources for the measured version.

The first live Gemini capture attempt stopped incomplete without a capture. It
revealed that the shared ToolNode limiter severed large JSON results, making
production snapshots unparsable. A subsequent fix clips text inside browser JSON
envelopes and preserves geometry/success metadata. The fixture snapshots in this
80-trial comparison were too small to expose that bug; the fix has regression
coverage. A second live attempt with this fix parsed the page observations but
stopped incomplete after 13 actions and two recovery attempts, without a capture.
The browser still repeated scrolling; the result must not be described as a
successful Gemini copy. Backend focused checks: 145 pass;
frontend suite: 299 pass; TypeScript and production Vite build pass.

The user-supplied share-link fallback also stopped incomplete after 14 actions,
without a content reference. No Google Doc was created. The signed-in browser
connection worked, but the local model/controller did not complete the requested
capture on either live surface. Keep this as a failed smoke test and a rollout
blocker, rather than substituting a manually extracted result for agent success.

Next tuning targets are simpler decision fields for the local model (it repeatedly
used `completed` where assessment required `success`) and making observed page
state dominate stale compact memory during recovery. Do not relax completion
evidence gates to hide these failures. Retest short and stale cases repeatedly,
then rerun the full suite before promotion. Ablations remain pending because this
candidate failed the no-regression criteria.

## Targeted follow-up: local-model workflow

The user clarified that success is saving the last message somewhere; a Google
Doc is unnecessary. A Gemini adapter now follows the known page structure and
observed down-arrow control through gated tools, with bounded loading waits and
completion/bottom checks. It returns the captured body reference immediately.
The general loop asks for only action and arguments; reporting fields are no
longer required, and model-authored memory is excluded from action selection.

Two live runs saved the same 5,702-character last response from the selected
conversation. The measured second run used nine tool actions and zero model
calls. A separate fresh browser read confirmed exact text equality, source URL
and SHA-256. The body is saved privately as `last-message.txt` alongside the live
trace. No Doc was created. This verifies the supported Gemini workflow, not
general autonomous browser performance.

Further diagnosis found the executor appended stale-reference recovery hints to
successful snapshots whenever page text contained “stale”, including fixture
URLs. Hints now require actual tool-error evidence. The original stale-case
regression included misleading harness feedback and must be interpreted with
that limitation. Production frame-prefixed refs are also accepted by capture,
panel scrolling and action fingerprinting.

Focused backend verification: 156 tests pass. Repeated short/stale comparisons
use the same fixture/model settings and include the executor hint correction in
both variants. The feature flag stays off until broader performance is reviewed.

The targeted follow-up finished 20 trials (two variants × two cases × five
repeats). Both the corrected legacy loop and new controller passed 5/5 short
tasks and 5/5 stale-reference tasks, with no detected false-success claims.
The new controller's earlier 0/5 regressions on each case are resolved in this
targeted run. Its broader eight-case performance has not been rerun after these
changes; do not combine the old suite and new subset into a single success rate.
