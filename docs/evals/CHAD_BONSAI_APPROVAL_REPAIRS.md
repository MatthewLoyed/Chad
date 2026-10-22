# Bonsai GUI approval follow-up — October 19, 2026

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Observed run

Saved GUI log conversation `4e9d5236-b184-45dd-b2f9-b66009ae94ad`, 15:40–15:46.
Private hashed log window: `.agent/experiments/chad-bonsai-approval/`.
No task replay or production restart was used for this analysis/repair.

The supervisor used Bonsai, searched local memory for Jordan, searched the web for
Meet, and delegated browsing. The browser used a saved LFM pin. It generated a
meeting-for-later link; this does not prove an active call was joined. The supervisor
first attempted a draft reply with invalid thread ID `None`, recovered to new-draft
creation, and hit the draft approval policy. After approval, it opened Prism, but
the old pending approval incorrectly paused the successful app call. Then a typed
planning-category `proceed` entered the planner. These are separate failures;
this run is not proof of a completed addressed draft or launched game instance.

## Implemented changes

- Remove new-draft creation from approval policy; keep sends gated, including the
  `send_gmail_message` alias. Reply drafts remain ungated.
- Clear checkpointed pause signals on new turns, new specialist invocations and
  successful tool-node results. A newly raised approval still overrides the reset.
- Short continuation replies inspect the same conversation's checkpoint and inherit
  execution mode before prompt/tool preparation. Unfinished plans and agent execution
  continue as agent work even when the GUI selector still says planning. Initial
  planner clarification and new substantive planning requests retain their own route.
- Browser model construction, model-ID resolution and residency share one policy:
  always use the supervisor model, ignoring stale browser pins. Other specialists'
  explicit pins remain supported.
- Preserve approval tool identity and argument previews through backend payloads and
  frontend message persistence. Match only the requested tool's events; never borrow
  Prism arguments for an email approval. Legacy unidentified approvals render generically.
  An old question event cannot replace an unrelated send approval with a question card.
- Draft approvals in historical traces display as drafts, not “Approve & Send Email.”

## Validation

71 backend tests passed across approval, policy, store, specialist, plan execution,
continuation and the prior GUI-harness regressions. Eleven frontend tests passed,
including the photographed wrong-action preview. TypeScript build check passed.
Another 36 neighboring isolation/MCP checks passed (107 backend tests total).
The frontend production bundle built successfully. No real email, meeting or app
action was replayed.

## Next behavioral work: reduce the instructions the user must supply

Keep Bonsai for the next controlled dev run; record both supervisor and browser model
IDs. This successful progress is promising, but different prompts and a mixed-model
browser do not establish a clean Bonsai-versus-LFM comparison.

Use progressively shorter prompts with the same task/environment and reset state:

1. Current detailed prompt, as a baseline after approval repairs.
2. Remove click-by-click browser directions; retain the intended outcomes.
3. Remove the explanation of a Meet code and the hint that Prism is a local app.
4. Remove the explicit reminder to search memory for Jordan's email.
5. Return to the original short request.

Score each outcome independently: active meeting verified (not merely a link for
later), code/link retained, locally grounded recipient, addressed Gmail draft,
Prism opened, requested instance launched. Reject guessed contacts, invented links,
and task-completion claims without tool evidence. Preserve failures and identify the
first missing capability/context at each stage. Public product grounding, local
person resolution and domain execution should supply these facts automatically;
avoid hardcoding this exact task as another special prompt.

Behavioral tests remain sequential on full dev Brain `:8788`; never restart `:8767`.
