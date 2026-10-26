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

**Feed** combines existing dashboard entries with autonomous run history. **For you**
shows discoveries; **Activity** shows the run log; **All** puts finds and connections
above activity. Automatic cards show labeled source excerpts; Chad can also
publish short summaries with source metadata, an optional source image, and a saved selection reason. **Read original** opens an HTTP(S)
source; **Discuss** appends an editable chat draft with the item's context;
**Dismiss** persists through the existing dashboard command and removes the card
only after success. Suggested actions are discussed in chat before execution.

The existing curiosity scavenger now also uses the discovery pipeline's dashboard
publication route (top three finds per pass). Interest tuning, verification, stash
and URL deduplication stay with that pipeline. Feed refresh never launches a search,
changes a schedule, or consumes the Telegram curiosity stash. Custom-topic stash
adds are not automatically published by this route. Existing dashboard thoughts,
life widgets, status entries and proposed actions are also visible in All.

Feed refreshes on opening and on Refresh, retaining cached items on failure.
Run history comes from `get_automations_summary` (currently 30 runs); quiet all-clear
checks remain hidden unless **Show quiet checks** is selected.

**Automations** owns recurring schedules, one-time tasks/reminders, event triggers,
and background checks; **Saved actions** retains the existing on-demand builder.
The internal `routines` route and API names remain compatible.

### Decisions and tradeoffs

The GUI separates updates (Feed) from configuration (Automations), inspired by
[Muse's background-task model](https://introducing.muse.ai/). Discovery and activity reuse existing producers and persistence. Fixed tool
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

## Interest Feed

Feed presents a scrolling stream of short posts: a title, a 2–3 sentence summary,
an original-source link, an image when available, and an info button explaining
why Chad selected it. **Discuss** opens an editable chat draft and preserves any
existing draft text. **Dismiss** persists only after the server confirms success.
Failed refreshes retain cached posts; broken images are omitted.
Sequential publication of an identical active discovery (same source URL, title
and summary) enriches its metadata instead of adding another recent card. This
handles a model correcting its interest reason; it does not enforce uniqueness
across concurrent writers or collapse different summaries of the same source.

The format draws on [Muse's interest feed](https://introducing.muse.ai/). Chad
uses its existing interest profile, curiosity/discovery pipeline, dashboard rows,
and automation run history. It does not create another scheduler or memory store.
The pipeline saves the actual matched topic/category as the selection reason.
Default interests must not be described as learned personal history.

Tech discoveries read the original page or a substantive RSS description, skip
unavailable/placeholder extracts, and preserve up to three source sentences within
`discovery.feed_preview_words`. These automatic posts are labeled source excerpts.
Source images come from Open Graph/Twitter metadata or RSS embedded images;
existing anime/video providers retain their cover/thumbnail metadata. Images
are optional and never generated or invented. The authoring tool also supports
short source-grounded summaries with separate source/image/reason metadata.

### Focused authoring turns

Call the existing turn coordinator with `category="feed"`. The lane keeps the
configured supervisor model and supplies only the current task, compact writing
instructions, and interest memory (bounded by `discovery.feed_memory_topics`).
It skips chat history, general graph/activity/Drive/session retrieval, history
compaction, intent/tool-selection model calls, specialist handoffs, and skill or
connector catalogs. Every job gets its own graph checkpoint. Its fixed tools are
`web_search`, `manage_curiosity`, and `update_dashboard`; only within-job tool
results accumulate. Ordinary chats mentioning Feed keep normal chat behavior.

A Feed task publishes through `update_dashboard` with `kind=discovery` and
metadata `content_type=source_summary`, `url`, `selection_reason`, and optional
`image_url`. A curiosity-stash add is not publication. Automatic scavenge still
uses the existing non-LLM discovery pipeline; opening or refreshing Feed never
starts a generation or alters automation schedules.

### Acceptance and boundaries

The v1 checks cover safe original links, source images and their failure state,
selection-reason disclosure, draft-preserving Discuss, persistent Dismiss, reload,
cached failure recovery and mobile layout through the actual development Brain.
Unit checks capture the final model request and verify the small tool/context
boundary. A live Bonsai 27B turn published a grounded two-sentence supplied-source
summary, with the saved interest reason separate from the body. That test is
not evidence that arbitrary independent research is always factually reliable.

Long-form articles, tutorials, multi-source synthesis and experiment suggestions
are outside this release. No extra editorial judge model or article-writing
pipeline is part of Feed. More/less-like-this controls, saved reads and project
recommendations remain future work. `evals/datasets/feed_quality.jsonl` holds
fictional short-post cases; live traces and personal preferences stay local.
