---
name: microsoft-assignment
description: "Set up a Microsoft Project assignment from Canvas: read the selected course assignment, identify chapter companion files, create its working folder, copy the required files, open them in a compatible Microsoft app, and open the textbook PDF in Chrome. Invoke /microsoft-assignment 5 for assignment 5. Setup only."
version: 1.0.0
tags: [canvas, microsoft, project, assignment, files, setup]
---

# Microsoft assignment setup

Use the assignment input from this invocation. `/microsoft-assignment 5` means
assignment 5, not chapter 5. Also accept phrases such as "assignment 5" or a
Canvas assignment link. Ask if the identifier is absent or ambiguous.

Stable resource settings are supplied separately in this invocation:
`course_name`, `workspace_root`, `companion_root`, `textbook_pdf`, `project_app`,
and `pdf_app`. If any needed setting is missing, ask for it rather than guessing
a personal path. These settings belong in private `config/preferences.yaml`
under `skill_settings.microsoft-assignment`, never in this shared guide.

1. Find the course in Canvas and read the selected assignment's current full
   instructions. Extract the assignment number, chapter, exercises, and exact
   companion-file requirements. Do not choose an assignment by due date.
2. Inspect the companion root and locate the actual chapter directory and named
   files. Chapter directories may look like `Ch05 Companion Files`. Do not infer
   the chapter from the assignment number or invent filenames/Canvas IDs.
3. Create or reuse `assignment<number>` under the workspace root. Copy only the
   required companions into it. Keep source files intact and preserve existing
   edited working copies; never overwrite those silently.
4. Verify the installed Microsoft application supports the selected files.
   `.mpp` files are Microsoft Project desktop plans. Planner itself does not
   natively import them. If the requested app is incompatible, report the mismatch
   and resolve it with the user; do not silently import, convert, or switch apps.
5. Open the working copies in the verified app, and the specified textbook PDF
   in Chrome (or the user's configured PDF app). Verify the actual documents,
   not just that a process launched. Navigate to a chapter/page only if grounded
   in the assignment or textbook.
6. Report the assignment, chapter, working folder, copied/opened files, app,
   and PDF. A missing prerequisite blocks its dependent actions. State partial
   completion accurately and identify what still needs attention.

This skill prepares the workspace. It does not solve, edit the contents of,
complete, or submit the assignment. Do not create a schedule or saved macro.
