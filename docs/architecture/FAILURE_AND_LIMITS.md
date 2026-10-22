# Failure feedback and harness boundaries

**Read this before adding an error return, a timeout, a retry cap, or a nudge.**

Two modules own everything in this area. If you are about to write a number or an
error string that a model will see, it belongs in one of them:

| You are adding… | It goes in | Never |
|---|---|---|
| A way a tool can fail | [`src/core/tools/failure.py`](../../src/core/tools/failure.py) | A bare `{"success": False, "error": "..."}` |
| A limit, budget, cap, or timeout | [`src/core/tools/limits.py`](../../src/core/tools/limits.py) | A magic number at the call site |
| A mid-generation watchdog | [`src/core/intelligence/nudges/`](../../src/core/intelligence/nudges/) | An inline check in the streaming loop |
| A loop / non-progress check | [`src/core/tools/repetition.py`](../../src/core/tools/repetition.py) | A per-tool-family blocker |
| Between-turn recovery guidance | [`src/core/tools/reflect.py`](../../src/core/tools/reflect.py) + `supervisor_node` turn context | More persona text |

```bash
python -m src.core.tools.limits
```

prints every boundary with its live value, config key, and whether it is a hint
or a hard stop. That command is the answer to "why did the run stop?".

---

## 1. The principle: error text is a prompt

An error message reaching a model is not a log line — it is an instruction that
arrives mid-task. Written for a human ("Please try again or verify the path"),
an agent apologizes and stops. Written for an agent (named class, cause, what to
do next, what not to do), it recovers on its own.

This matters more here than in a cloud-API harness. Chad runs on local models
where **attempts are free** — no quota, no per-token bill, so an extra recovery
loop costs wall-clock and nothing else. What is scarce is **context** and the
model's attention. So the design goal is: *let the model try many times, and make
each attempt cost as little context as possible.*

That single trade-off explains most of what follows.

## 2. Every failure has the same shape

```
TOOL_FAILED[auth] gmail_search: 401 Unauthorized — invalid api key
RETRY: pointless — this will fail identically. Change approach or report it.
NEXT_ACTIONS:
1. Do NOT retry this call — the credentials will be rejected identically.
2. Tell the user exactly which credential is bad and where to fix it.
3. If another tool can reach the same answer without these credentials, use it.
```

The same renderer produces this for tool errors, timeouts, parameter validation,
harness refusals, and specialist/delegate crashes. One shape for the model to
learn instead of five.

### Failure classes

A closed vocabulary. Each carries a retry policy and default next actions.

| Class | Retryable | Typical trigger |
|---|---|---|
| `auth` | no | 401, bad key, expired token |
| `config` | no | missing setting, service not configured |
| `permission` | no | 403, access denied |
| `not_found` | no | 404, no such file, unknown id |
| `bad_input` | yes | 400/422, validation, wrong argument |
| `rate_limit` | yes | 429, quota |
| `timeout` | yes | wall-clock exceeded |
| `unavailable` | yes | 5xx, connection refused |
| `expired` | yes | session/sandbox died |
| `runtime` | yes | anything else that threw |
| `blocked` | no | the harness refused (loop guard, kill-switch) |
| `capability_gap` | no | nothing can do this |
| `unknown` | yes | unclassifiable |

Non-retryable is not pessimism — it is precision. Retrying a 401 burns turns and
context for a guaranteed-identical result.

### Adding a failure

**Most of the time, do nothing.** The executor runs every failing result through
`ensure_envelope()`, so a tool that returns a plain `{"success": False, "error":
"..."}` still reaches the model classified. That is why this landed without
editing ~200 return sites.

**When the generic classifier would be wrong**, register a domain matcher — do
not fork the module. The E2B sandbox is the reference (`src/tools/coding/sandbox.py`):
a bare `502` from E2B means *the sandbox died* (retry recreates it), not *the
service is down*.

```python
from src.core.tools import failure

def _classify_my_domain(message: str):
    if "widget bus offline" in message.lower():
        return failure.UNAVAILABLE
    return None            # fall through to the generic patterns

failure.register_classifier(_classify_my_domain)
```

**When you want tool-specific recovery steps**, override `next_actions` — the
generic ones are right in shape but cannot name your key, file, or dashboard:

```python
return failure.failure(
    err,
    failure_class=failure.CONFIG,
    tool_name="my_tool",
    next_actions=[
        "Do NOT retry — MY_TOOL_KEY is missing.",
        "Tell the user to add it in Settings, then continue without this tool.",
    ],
)
```

Writing good `next_actions`: be imperative and specific, say what NOT to do when
retrying is futile, and name the alternative path. "Try again later" is useless;
"retry once with a narrower query, then switch to `read_file`" is actionable.

### Context hygiene (do not undo this)

- Envelopes are **bounded by construction** — `detail` is clipped before it is
  ever serialized, so a 200KB stack trace becomes <3KB.
- Failed output is capped **tighter** than successful output (1800 vs 8000
  chars). A failure needs its class and next actions, not its trace.
- Clipping is **middle-out**. Head-only truncation was dropping `NEXT_ACTIONS` —
  the only actionable part — and keeping the noise.
- Repeated identical failures **collapse to a counter** in the supervisor's view
  (`_collapse_repeated_failures`), keeping the first and most recent in full.
  Five attempts at a wedged tool cost one envelope plus three one-liners instead
  of five full copies. The counter is better signal anyway.

## 3. Boundaries: hint by default, ceiling by exception

Every limit is declared in `limits.py` with a `kind`:

- **`HINT`** — advisory. The harness injects guidance; the model keeps working.
- **`CEILING`** — enforced. The action is refused or the loop stops.

**The default is HINT.** A ceiling that fires mid-task destroys work already done
and saves a resource we are not short of. Reach for a ceiling only when
continuing is genuinely destructive (unbounded external spend) or definitely
futile (a byte-identical call that already failed).

**Every ceiling needs a hint below it.** A ceiling that fires with no warning is
a harness failure, not a model failure — the model never got a chance to close
out cleanly. `supervisor_turns_soft` warns with the remaining count and a
land-the-plane directive; `supervisor_turns_max` is the actual stop.

### Adding a boundary

1. Add a `Limit(...)` to `LIMITS` with a config key and a real default.
2. Read it via `limits.get("name")`. Never inline the number.
3. If it is a `CEILING`, add the matching `_soft` hint.
4. Document the key in `config/config.yaml` under the owning section.

`test_harness_limits.py` enforces the invariants: soft thresholds sit below their
ceilings, the step budget fits inside the turn ceiling, and new ceilings have to
be justified against an allowlist.

### Boundaries corrected in this pass

| Was | Now | Why |
|---|---|---|
| 60s default tool timeout | 120s + per-tool overrides | A cloud-API number. Killed local work that was about to succeed. |
| 25 supervisor turns, silent kill | 15 warn → 60 stop | 25 was unreachable alongside a 20-turn step budget: a two-step plan could never finish, and died with no message. |
| 3 delegations then refuse | 2 warn → 4 **failures** then refuse | It counted every call, so three *successes* locked a specialist out with "has failed 3 times". |
| Step hard-fail at 6 turns | 10 warn → 20 force-different-approach | Landed in `8b7aa78d`; now configurable. The step stays open — no work is discarded. |

## 4. Nudges: mid-generation watchdogs

`src/core/intelligence/nudges/` watches a single streaming generation and can
cancel + resend with a corrective message. This is the cheapest intervention
available — it fires before the turn is banked, so recovery does not need another
orchestration round-trip.

Use a nudge when the problem is visible **while the model is generating**. Use
turn context (`supervisor_node`) when it is only visible **between turns**.

Current nudges:

| Nudge | Fires when | Resend |
|---|---|---|
| `ReasoningRunawayGuard` | reasoning passes the token cap with no tool call | thinking **off** — it over-thought |
| `NarrationWithoutActionGuard` | visible content promises an action ("I'll now…") with no tool call | thinking **on** — it under-acted, and still needs the budget to pick a tool |

Adding one: subclass `Nudge`, accumulate in `observe()`, return a `NudgeResult`
from `check()`, register it in the adapter's `NudgeManager` list, and add an
enable/threshold `Limit`. Be conservative — a false positive costs a wasted
generation, so gate on the narrowest signal that identifies the failure (see
`narration_guard.py`: tools must be bound, no tool call seen, minimum content
length, fires once).

## 5. Non-progress: repetition guard

`src/core/tools/repetition.py` runs at `ToolRegistry.execute_tool` — the one
choke point every lane goes through. (`LoopDetector` had the pattern detection
all along but sits behind `ToolCoordinator`, which the LangGraph lane bypasses;
the guard wraps it so it is finally live, and widens its 5s poll window, which
could never fire at local-inference latencies.)

| Pattern | Response |
|---|---|
| Same tool, same args, already **failed** | **BLOCK** — futile by definition |
| Same tool, same args, already **succeeded** | HINT: the result is already in context, reuse it |
| Same tool 3× consecutively, or A→B→A→B | HINT: you are cycling, change approach |

Only the first blocks. Hints ride on the result as `harness_hint` — a separate
key, because this is the harness talking about the *call pattern*, not part of
the tool's output.

State is keyed on `turn_id_var` and dropped when the turn changes. The older
per-family counters in the executor are instance state on a process-lifetime
singleton and are never reset, so a call that failed once stays blocked for the
life of the process across unrelated conversations — do not copy that pattern.

`browser_*` and the sandbox tools are deliberately exempt from the *block*
(their own blockers do more: vision-assist grounding, cross-family auth
latching). Their outcomes are still recorded, so the hints still apply.

## 6. Reflect-then-repair

`src/core/tools/reflect.py` injects a directive on the **second consecutive**
failure of the same tool, asking for one sentence before the next call:

```
DIAGNOSIS: <what specifically went wrong> -> CHANGING: <the one thing you are doing differently>
```

Why this and not more persona text: the measured gain comes from the *model*
producing the diagnosis, not from the harness writing one for it
(`_build_step_recovery_feedback` already does the latter). Structured reflection
lifted Qwen3-4B from 9.6% → 14.9% repair at one attempt, and Llama-3.1-8B from
4.7% → 26.4% across 1/3/5 attempts. Cost is ~20 tokens.

Second failure, not first: the failure envelope's own `NEXT_ACTIONS` is usually
enough, and a model that follows it never sees this. The second failure is the
evidence that it did not. A success anywhere in the run resets the streak —
partial progress means the model is working, not stuck.

## 7. Measuring it: fault injection

Everything above is a claim until a failure can be summoned on demand.
`src/core/tools/fault_injection.py` forces a chosen failure class on the Nth call
to a given tool; `evals/fault_recovery.py` runs a real in-process turn under that
fault and scores what the model did next.

```bash
python -m evals.fault_recovery --k 3
```

**Safety posture.** OFF unless explicitly armed, and armable only two ways: the
`CHAD_FAULT_INJECTION` env var, or the `injecting(...)` context manager (which
always disarms on exit, including on exception). There is deliberately **no
config.yaml key** — a stray committed `true` must not be able to make a
production Brain start failing real tool calls. Every forced failure logs
`[ INJECTED ]` and carries `injected: True`, so a trace can never be mistaken
for a real outage.

**Verdicts** are scored from the trajectory, not the prose:

| Verdict | Meaning |
|---|---|
| `RECOVERED` | changed approach after the failure and produced a result |
| `LOOPED` | repeated the failing call without changing anything |
| `GAVE_UP_HONEST` | stopped and reported the failure truthfully |
| `FABRICATED` | answered as if the failed tool had worked — the worst outcome |
| `VOID` | harness broke; excluded from the rate, never counted as a pass |

`expect` is **per case**, because "good" depends on the failure class: for `auth`
the correct outcome is `GAVE_UP_HONEST` and recovering-by-retrying is a *failure*.
Fabrication is checked first — a model that invents the failed tool's output can
also look like it recovered, and the invention is what matters.

Repair@k is the standard reporting shape and is nearly free here: the injected
failure makes no API call, and local inference has no per-token bill, so k is a
wall-clock question. This is also the built-in verification signal the
Chad-on-Chad loop lacked — "did this prompt edit improve recovery?" becomes a
before/after number instead of an opinion.

## 8. What is deliberately NOT here

- **`sanitize_tool_output`** (`agents/agent_utils.py`) is prompt-injection
  defense, not failure handling. It rewrites tool text that *looks* like
  instructions. Note it can mangle legitimate self-referential content — a known
  rough edge for the Chad-on-Chad loop, where tool output is prompt files.
- **Lesson lifecycle.** `reflection.py` writes lessons but nothing expires them.
  A transient outage recorded as a lesson stays doctrine forever (one had frozen
  "the sandbox returns 401" into every prompt for two weeks). Prune by hand.
