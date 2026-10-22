---
name: workflow_routine
description: "Define and run a repeatable multi-step routine — a morning briefing, a weekly review, a day/week calendar plan, or any 'do these steps in order every time' sequence. Use when the user wants to set up, run, or automate a recurring routine, plan their day/week around their calendar, or asks for something 'every morning / each week'. For a one-off multi-step goal use `planning`; for pure timing use a scheduled task."
version: 1.1.0
license: MIT
tags: [routine, workflow, calendar, automation, productivity]
---

# Workflow / Routine Runner

Assemble and run a **repeatable sequence of steps** that produces a useful artifact — a
morning briefing, an end-of-week review, a planned-out day. A routine is the *content and
order* of the steps; scheduling (when it fires) is a separate, optional layer.

## When to use vs. neighbors
- **This skill** — a recurring, multi-step sequence with a defined output ("every morning:
  calendar + urgent email + top tasks → briefing").
- **`planning`** — a one-off goal you break down once.
- **A scheduled task** — just the *timing* trigger. Pair it with this skill to run a routine
  on a schedule; this skill defines *what runs*.

## Standard Operating Procedure

### 0. Choose the execution form
The Routines workspace currently saves **literal tool calls**, not an agent plan.
Use it only when every parameter is already known and no step needs to interpret
earlier results. Drafting those calls uses the selected supervisor model.

For a procedure such as "read my Canvas assignment, find the chapter's companion
files, prepare a folder, and open them", follow this skill in an agent chat. Record
the stable instructions and resources, accept the assignment as input each run,
and resolve chapter numbers, file names, and IDs from live evidence. Do not save
invented IDs or placeholders as executable parameters. A `load_skill` step only
loads text; the workspace runner does not then follow that text automatically.

Define the required inputs, stable resources, ordered dependencies, success checks,
and behavior when an input or resource is missing. Do not claim the procedure has
been saved unless a persistence tool actually confirms it.

### 1. Identify the routine
Name it and its output. Is it **on-demand** ("run my morning briefing now") or
**recurring** ("set this up for every weekday at 7am")? Recurring → offer to attach a
scheduled task after the routine is defined.

### 2. Define the steps
List the ordered steps and the tool each uses. Example — *Morning Briefing*:
1. `google_calendar_check` → today's meetings.
2. `gmail_check_inbox` / `search_gmail` → urgent unread (see `inbox_triage`).
3. `todoist` (or task source) → today's top tasks.
4. Optionally `web_search` → weather / one relevant headline.

### 3. Gather (linear, one pass per step)
Run steps in order, collecting results. If an independent briefing source fails,
note that section as unavailable and continue. If another step depends on the
failed result (for example selecting files from assignment instructions), stop the
dependent actions and report the missing prerequisite. Never report full success
for partial work. Use `error_recovery` for bounded recovery.

### 4. Assemble the output
Compose one clean artifact, not a wall of raw tool dumps:

```
☀️ Morning Briefing — <date>
📅 Calendar: <N meetings; first at …>
📧 Urgent email: <count + 1-liners, or "nothing urgent">
✅ Top tasks: <3 items>
🌤 <weather / one headline, if included>
```

### 5. (Optional) Make it recurring
If the user wants it automated, confirm the schedule and create a scheduled task that
triggers this routine. **Creating a standing schedule is a persistent change — confirm the
exact time/cadence before setting it.**

## Calendar-planning variant
For "plan my day/week": `google_calendar_check` → lay out fixed commitments → slot the
user's tasks/goals into the free gaps → present the timeline → ask before writing anything
back to the calendar (event creation is an outward action — gate it).

## Local-model notes
- Keep the procedure's order stable. Resolve assignment-specific values each run
  when following a skill in chat; literal saved steps do not have this capability.
- Verify outcomes, not just attempted tool calls. A failed prerequisite blocks its
  dependent actions; independent briefing sections can degrade gracefully.

## Microsoft project-assignment setup variant
Use the course and assignment selected by the user, an output root, companion-file
root, and textbook PDF path as inputs. If the assignment is not identified, ask
for it rather than picking the newest or assuming a chapter from its number.

1. Read the selected Canvas assignment and extract chapter, exercises, named files,
   and any naming requirements. Resolve ambiguity before creating the workspace.
2. Locate the matching chapter directory and exact required companion files.
   Verify paths exist; chapter number and assignment number are separate values.
3. Create or reuse `assignment<number>/` under the output root. Copy only needed
   companions into it; retain existing edits and leave originals untouched.
4. Inspect the installed Microsoft application and file associations. `.mpp` is
   a Project desktop format; do not assume the Planner app opens it. Report an
   application mismatch and use a compatible app only within the user's request.
5. Open working copies in the verified compatible app and the supplied textbook
   PDF in Chrome. Verify each launch; opening a process alone is insufficient.
6. Report the Canvas assignment, chapter, workspace, copied files, application,
   and PDF. Setup is complete only when those outcomes are verified. Do not
   complete or submit coursework as part of this setup-only routine.

## Common pitfalls
- ❌ Continuing dependent actions after a prerequisite failed, or treating partial work as complete.
- ❌ Silently creating a recurring schedule without confirming time/cadence.
- ❌ Writing calendar events without approval.
- ❌ Emitting raw tool JSON instead of an assembled, readable artifact.
