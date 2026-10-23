# Routines, skills, and automations

Chad's current saved routines are ordered, literal tool calls. The GUI calls
these **Saved actions**, under **Automations**, alongside **Schedules & triggers**.
Saved actions and `run_saved_routine` use the same executor. Parameters do not support
variables, references to previous results, or agent reasoning. Labels and
descriptions are display text. Creating a routine does not execute it.

Routine creation uses the supervisor's `agent` model from the existing model
router, including its provider, endpoint, credentials, and model profile. It does
not use the selector model, stale profile pins, or the first loaded LM Studio
model. Calls are asynchronous. Drafts include complete tool schemas and are
validated before being returned; an unknown tool or invalid parameter rejects
the entire draft. An incomplete generation is also rejected.

The executor stops on a failed prerequisite by default, including tools that
return `success: false` or MCP `isError: true` without raising an exception.
If continuing is explicitly enabled, the overall outcome still reports failure.

## Choosing a representation

- **Skill:** reusable instructions, resources, and decision rules followed by the
  agent. Use when each run requires interpreting new data or choosing actions.
- **Routine:** a reusable user-invoked workflow. Today's saved-routine implementation
  handles fixed calls; adaptive procedures currently run as skills in chat.
- **Automation:** the trigger and delivery configuration (time or event) for work
  that should run without a fresh request. It can invoke a skill-backed task.

Terminology differs by product. UiPath describes on-demand routines implemented
as skills, while Anthropic describes cloud routines with schedules/API/events.
Neither implies that Chad's literal-step runner can execute an adaptive plan.

## Navigation and activity

**Feed** presents the existing autonomous run log, newest first. It refreshes
when opened and on Refresh, showing the latest batch supplied by
`get_automations_summary` (currently 30 runs). Quiet all-clear checks are hidden
unless **Show quiet checks** is selected. Failed refreshes show an error while
retaining cached activity. Feed does not create suggestions, goals, or schedules.
**Automations** owns recurring schedules, one-time tasks/reminders, event triggers,
and background checks; **Saved actions** retains the existing on-demand builder.
The internal `routines` route and API names remain compatible.

### Decisions and tradeoffs

The GUI separates updates (Feed) from configuration (Automations), inspired by
[Muse's background-task model](https://ai.meta.com/muse/). This is an initial
activity feed, not Muse feature parity or a new autonomy engine. Fixed tool
sequences remain available as saved actions rather than a primary destination.

## Assignment-setup acceptance case

The user selects a Canvas course and assignment. Stable resources are an output
root, a companion-file root, a textbook PDF, and preferred applications.

Read the assignment; identify its chapter, exercises and companion files; create
or reuse the correctly numbered assignment folder; copy only required files;
open working copies in a compatible Microsoft application; open the textbook
in Chrome; verify all outcomes and report them. Existing work and source files
must be preserved. Assignment number does not imply chapter number.

This is an **adaptive, on-demand routine backed by a skill**, with no schedule
implied. `workflow_routine` contains the procedure for agent chat. The fixed-step
builder should reject requests that require unknown Canvas IDs or filenames.

The saved-routine feature still needs an agent execution mode and per-run input
binding before this case can run from its Run button. Merely saving a `load_skill`
call would load instructions and stop, not execute the assignment setup.

Project desktop's `.mpp` files require a compatible application. Microsoft's
Planner FAQ says native `.mpp` import is unavailable in Planner itself and points
to the Planner Power App. Verify the installed app rather than treating Planner
and Project desktop as interchangeable.

## Sources checked 2026-10-20

- [UiPath: repeating work with routines](https://docs.uipath.com/delegate/standalone/latest/user-guide/routines)
- [Anthropic: introducing routines](https://claude.com/resources/articles/introducing-routines-in-claude-code)
- [Agent Skills specification](https://agentskills.io/specification)
- [Microsoft Planner FAQ](https://support.microsoft.com/en-US/Planner/frequently-asked-questions-about-microsoft-planner)
- [Project desktop file formats](https://support.microsoft.com/en-us/project/file-formats-supported-by-project-desktop)

Personal course links, usernames, and filesystem paths belong in private runtime
configuration or user-saved routines, never in tracked documentation or tests.
