# Chad-on-Chad self-improvement loop (issue #358)

**DEV-ONLY.** This is a maintainer tool for improving Chad using Chad. It must not ship in a public
release — see [`evals/SELF_EVAL_DO_NOT_SHIP.md`](../../evals/SELF_EVAL_DO_NOT_SHIP.md). Related:
[`CHAD_CODE.md`](./CHAD_CODE.md) (the coding worker), epic #569, `docs/features/CAPABILITY_SELF_IMPROVEMENT.md`
(the *product* self-improvement, a separate system).

## Maintainer priority — identification first (2026-10-19)

The primary deliverable is **testing → concrete/behavioral bug identification →
evidence-backed analysis → useful triage and prompts**. Finding Chad's errors is
valuable independently of Chad's ability to repair them. A maintainer may hand a
reproducible finding to Codex or another capable cloud model for a quick fix.
Local self-repair and matched before/after proof are optional extensions, not the
definition of success. Do not let a missing repair/check policy block identification.

Use the [labeled phase plan](../evals/CHAD_SELF_EVAL_NEXT_STEPS.md): four core phases
(evidence, detection/reproduction, analysis/handoff, repeatable discovery), followed
by optional repair. The repair-oriented workflow below remains historical design
and optional integration work; it must not override this priority.

## Where this sits vs LLM-as-judge

Do **not** blur layers — see the division table in
[`evals/HARNESS_LESSONS.md`](../../evals/HARNESS_LESSONS.md). Short version: `evals/judges.py`
**analyzes** (0–10 + classify); the core self-eval path adds concrete failure detection,
reproduction and evidence-linked handoff. Optional repair closes the larger flywheel
in **#358**. Agent runbook: [`evals/AGENTS.md`](../../evals/AGENTS.md).

## The idea

Verification update (2026-10-18): the driver now gates replay on terminal successful
worker status and a captured patch, preserves reports across failures, and requires
an independent verifier for `ready_for_review`. A model judge alone is advisory.
See [verification gates and remaining work](../evals/CHAD_SELF_EVAL_VERIFICATION_GATES.md).

Chad improves Chad the way a contributor would: **observe a failing eval → diagnose it → make a
fix → re-run the eval → judge whether it improved.** The division of labor is deliberate:

- **Chad (base model)** is the **diagnostician, judge, and orchestrator.** Reading a trace, root-
  causing behavior, and deciding whether a fix helped are *reasoning* tasks — the big model's
  strength. Chad is the judge.
- **Chad Code (local 35B in Docker)** is the **hands.** It only edits code/prompts. It does not
  decide what's wrong or whether the result is better.
- Independent operator-owned checks establish observed failures; model diagnoses and
  causes remain advisory. Human/Codex/cloud repair is a valid use of the finding.
  A fully local Chad-on-Chad repair is one optional test, not a required outcome.

```
observe eval → Chad diagnoses → Chad Code edits (in its clone) → pass edit back
   → harness re-runs eval (change live) → Chad judges before/after → report
```

## In-process evals (no server)

**Current verification limit (2026-10-19):** use the full worktree Brain/WebSocket
path on 8788 for behavioral tests. The in-process design below has not completed
the current behavioral baseline reliably on this machine. Use explicit stage budgets,
retain infrastructure timeouts separately, and never restart production 8767.

The key simplification. A full multi-agent turn does **not** need the websocket server:
`TextModeClient.send_message()` dispatches through the same `TurnCoordinator` the server uses — the
server is pure transport. So a real turn (supervisor → delegate → tools) runs **in-process** against
whatever checkout the code is imported from.

- Runner: [`evals/inprocess.py`](../../evals/inprocess.py) → `run_turn_inprocess(prompt)`.
- Core is built by `SystemFactory.build_core_engine(config, event_bus)` (the real LiteLLM adapter +
  `TurnCoordinator`), warmed by `warmup_async_infrastructure`.
- Trace is read off `turn_coordinator.last_turn_thinking_events` (consolidated reasoning +
  `tool_start`(params) + `tool_end`(results) + plan, via `turn/event_capture.py`) and
  `last_turn_metrics` (tokens, `stop_reason`, counts).
- **No wall-clock timeout** on the turn — a real local-model turn can be 20+ minutes; a timeout
  silently kills legitimate runs.

This mirrors how DSPy / TextGrad / SICA do self-improvement (prompt-as-parameter, in-process). The
earlier "boot a throwaway Brain from the clone" design was over-engineered and is not needed.

## Diagnosis + judging

Chad uses the [`eval_diagnosis`](../../src/skills/eval_diagnosis/SKILL.md) skill: read the whole
trajectory (reasoning first), validate claims with real `list_skills`/`search_for_tool` calls
(don't fabricate), propose a **generic** fix (never overfit to one case), and classify it —
prompt/behavior change, new capability (→ file an issue), or code bug fix. The judge step is another
Chad turn comparing before/after traces; it must ask for a short verdict first (a local model can
burn its budget reasoning and hit `stop_reason: length`, so also capture the reasoning, not just the
final response).

## Proven end-to-end (2026-07-23)

On the "find my smart annual report and check for advisor initials" behavioral eval:

| | Before | After Chad Code's fix |
|---|---|---|
| Tools | 56 | 7 |
| Duration | ~23 min | ~3 min |
| Behavior | spiraled running sandboxed scripts to read a local PDF | pivoted to asking the user instead of impossible local ops |

Chad diagnosed the root cause (the sandboxed `code_agent` can't read the user's local filesystem),
Chad Code added one **generic** routing rule, and Chad judged the result `IMPROVED` with correct
evidence. Model competence was not the bottleneck.

### Honest caveats
- **Non-deterministic** failure + single before/after run — the improvement is real on the *spiral*
  axis but noisy; a rigorous claim needs multiple runs each side.
- The fix made Chad fail **gracefully**, it did not **solve** the task — reading a local PDF is a
  genuine capability gap (the `pdf` skill exists but isn't used effectively). That's a "file an
  issue," not a prompt fix.

## Clone provisioning (issue #596)

The Chad ↔ Chad Code handoff does **not** break on the model — it breaks on making the coder's clone
a faithful behavioral-eval environment. `provision_eval_clone` in
[`src/tools/dev/eval_clone_provisioner.py`](../../src/tools/dev/eval_clone_provisioner.py) does all
four steps that were previously hand-fixed once during the #358 proof run:

1. **Pins the clone to the eval's SHA** via `git worktree add --detach <dest> <sha>` (fetches from
   origin first if the SHA isn't local yet) — so before/after are attributable to the fix, not to
   `dev` drift. Idempotent: re-provisioning the same `job_id` reuses the worktree if it's already at
   that SHA, otherwise recreates it.
2. **Copies the `config/persona` overlay** (the rich live prompts) into the clone — a bare checkout
   only has the generic baseline in `prompts/persona`, which is a different, degraded Chad.
3. **Allowlists the clone** for Chad Code to write into — returns an `allowlist_env` dict
   (`CHAD_CODE_EXTRA_ALLOWLIST`) for the dispatching process's environment, consumed by
   `openhands_dispatch._workspace_guard`. This is a **per-process env grant, not a config.yaml
   edit** — `openhands_workspace_allowlist` stays `[]` for every other coding job (#576's epic
   non-goal: monorepo not writable by default).
4. **Never copies secrets in.** `config/secrets.yaml` is gitignored, so a `git worktree add`
   checkout never contains it — the coder container genuinely never sees a token. Instead
   provisioning returns `secrets_env` (`CHAD_SECRETS_PATH` pointing at the HOST checkout's
   `config/secrets.yaml`) for the **in-process eval** (not the coder) to read real secrets when it
   runs with `cwd=<clone>`.

```python
from src.tools.dev.eval_clone_provisioner import provision_eval_clone, deprovision_eval_clone

result = provision_eval_clone(sha, job_id="my-eval-run")
# result: {success, path, sha, persona_copied, allowlist_env, secrets_env}
# merge allowlist_env into the env before dispatch_coding_task(workspace=result["path"], ...)
# merge secrets_env into the env before running the in-process eval with cwd=result["path"]
...
deprovision_eval_clone(result["path"])  # git worktree remove when the eval cycle is done
```

## How to run it (maintainer)

Free the GPU (one model fits at a time), then:

```powershell
# in-process eval (before), from the checkout under test
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -c "import asyncio; from evals.inprocess import run_turn_inprocess; print(asyncio.run(run_turn_inprocess('<prompt>')))"

# Chad Code makes the fix in its clone (allowlist the clone, coder edits a prompt/code file)
# then copy the edited file back into the eval checkout and re-run the in-process eval.
```

The scratch drivers used in the first run were removed; the durable pieces are `evals/inprocess.py`,
the `eval_diagnosis` skill, and the `CHAD_SECRETS_PATH` override.
