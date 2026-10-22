# Slash skills

Type `/` in the composer to discover skills and direct tool shortcuts. Skills use
the book icon; shortcuts use the lightning icon. Filter by typing a name, choose
with click, Tab, or arrows and Enter, then add inputs before sending.

- `/skills` opens Settings → Advanced → Skills in the GUI. The backend also
  supports `/skills` as a text listing for clients without that navigation.
- `/microsoft-assignment 5` runs the Microsoft assignment setup skill with 5 as
  the assignment input. `assignment 5` or an assignment link also work as input.
- `/workflow-routine <request>` invokes the `workflow_routine` skill. Hyphens
  and underscores are accepted as aliases; names are case-insensitive.
- Settings → Skills → select a skill → **Use in chat** inserts its command into
  the composer. This does not execute it until the user sends the message.

The skill catalog currently supports browsing, previewing, and invocation, not GUI
authoring. New built-in skills still require a `src/skills/<name>/SKILL.md` and an
entry in the skill allowlist. Disabled/disallowed skills cannot be invoked.

## Execution

The Brain resolves the command against the registered catalog and loads that
skill's full guide. Arguments are plain text data for the supervisor, not shell
code or literal tool parameters. Missing/ambiguous inputs are resolved according
to the guide. Explicit skill requests run in the agent lane using the supervisor
model, even when Ask/Auto was selected. The tool selector sees the skill's purpose
and arguments, so it can select Canvas, file, and application tools rather than
trying to classify a bare slash alias.

The selected guide and this invocation's arguments are supplied through the
existing active-skill state. They take precedence over automatically bundled
skills. A new explicit invocation starts its own task rather than continuing an
old plan or pending tool approval. Skill runs bypass the semantic response cache:
repeating a setup command must inspect the current assignment and files again.
Restricted contact/Telegram sessions keep their existing tool limits and cannot
use slash skills to enter the desktop agent lane.

Ordinary chat is unaffected. Unknown or invalid slash names return guidance
without executing a skill. Exact direct-tool shortcuts remain available; adding
arguments no longer silently invokes a shortcut that would discard those inputs.

## Private resource settings

Stable resources belong in gitignored `config/preferences.yaml`, for example:

```yaml
skill_settings:
  microsoft-assignment:
    course_name: Example Project Management
    workspace_root: C:/Assignments
    companion_root: C:/Textbook/Companion files
    textbook_pdf: C:/Textbook/Project.pdf
    project_app: Microsoft Project
    pdf_app: Chrome
```

Only settings for the explicitly invoked skill are included in its runtime guide.
No personal paths belong in tracked skill files or tests. Assignment number and
chapter number are separate; the chapter must come from current Canvas
instructions. The skill preserves existing edited files and the original
companions, verifies a compatible Microsoft app (Planner itself does not natively
import `.mpp`), and performs setup only, without completing or submitting work.

Fixed tool sequences remain under Routines. Skills are the reusable path for
procedures that interpret new inputs and choose actions each run. Scheduling is
handled separately by automations.
