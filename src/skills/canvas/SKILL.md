---
name: canvas
description: Canvas LMS toolchain for querying courses, assignments, syllabus, grades, modules, files, announcements, and lecture content.
subagent: canvas_agent
metadata:
  version: 1.0.0
  category: education
  tags: [canvas, lms, assignments, grades, syllabus, modules, files, homework]
  subagent: canvas_agent
---

# Canvas LMS Skill Guide

This skill guides your use of Canvas LMS tools. All Canvas tools support natural names (course titles, assignment titles) as well as numeric IDs with built-in fuzzy matching. Canvas operations are strictly **READ-ONLY**.

---

## 🛠️ Canvas Tool Index & Intent Mapping

| User Intent | Primary Tool | Key Parameters / Usage |
| :--- | :--- | :--- |
| **Get Assignment Details / Prompt** | `get_assignment_details` | `assignment="Assignment 4"` (auto-searches across all active courses if `course` is omitted). |
| **Search / List Assignments** | `get_canvas_assignments` | `search_term="Assignment 4"` (cross-course), or `course="Manage Software Project"`. |
| **What's Due Soon / Dashboard** | `get_upcoming_assignments`, `get_canvas_dashboard_todos` | Shows upcoming deadlines, undated work, and to-do items. |
| **Read Attached PDF / Handout** | `read_course_file` | `file_id=12345` (extracts text from assignment prompts or slides). |
| **Find Lecture Files / Downloads** | `get_course_files` | `course="Manage Software Project"`, `search_term="Chapter 3"`. |
| **Check Grades & Rubric Feedback** | `get_assignment_submission` | `assignment="Assignment 4"`, `course="..."` (shows submission text, score, feedback comments). |
| **Course Overall Grade Summary** | `get_course_grades` | `course="Manage Software Project"`. |
| **Modules & Lecture Topics** | `get_course_modules`, `get_module_items` | Explore weekly modules, reading lists, and published units. |
| **Course Syllabus & Policies** | `get_course_syllabus` | `course="Manage Software Project"` (grading scales, course policies). |
| **Find Instructor / TA Contacts** | `get_course_people` | `course="Manage Software Project"`, `role="teacher"`. |
| **Discussions / Announcements** | `get_course_discussions`, `read_discussion_topic` | Course Q&A, project updates, announcements. |

---

## 📖 Best Practices & Playbooks

### 1. Single-Step Assignment Lookup (Fastest & Preferred)
When asked for assignment details, instructions, or deadlines (e.g. *"What are the instructions for Assignment 4?"* or *"Tell me about Assignment 4 for Manage Software Project"*):
- **Call `get_assignment_details` directly.**
  - If course is known: `get_assignment_details(course="Manage Software Project", assignment="Assignment 4")`
  - If course is omitted: `get_assignment_details(assignment="Assignment 4")`
  The tool will automatically search across all active courses and return the full prompt, due date, points, and attachments in **a single turn**.

### 2. Reading Attached Assignment Files
If `get_assignment_details` indicates instructions are in an attached document or lists items in `attachments`:
- Inspect the returned `attachments` list for `file_id`.
- Immediately call `read_course_file(file_id=...)` to extract and inspect the document text.

### 3. Exploring Modules vs. Assignments (Lectures vs Homework)
- Canvas organizes formal submissions (homework, projects, quizzes) under **Assignments**.
- Course modules (`get_course_modules`, `search_module_items`) are for **lecture slides, notes, weekly units, and reading PDFs**.
- **Negative Constraints**: If the user asks for lecture materials or explicitly says *"not assignments"* / *"download lectures, not homework"*, do **NOT** query assignments or download assignment attachments. Look exclusively in **Modules**.
- **Finding Lectures**:
  1. Use `search_module_items(course="...", query="lecture")` or `get_course_modules(course="...")`.
  2. Inspect module items of type `File`.
  3. Download via `download_course_file(file_id=..., destination_path=...)` or read via `read_course_file(file_id=...)`.

### 4. ⚠️ Handling Locked Files (HTTP 403 Forbidden)
- Canvas course instructors frequently lock the root `/files` tab from student API tokens.
- Calling `get_course_files` will return `CANVAS_LOCKED_FILES` (HTTP 403 Forbidden) when this tab is disabled.
- **Recovery Playbook**:
  1. **Do NOT give up or fail.** This is normal Canvas instructor configuration.
  2. All slides, handouts, and PDFs are published inside **course modules**.
  3. Pivot immediately to `search_module_items(course="...", query="...")` or `get_course_modules(course="...")`.
  4. Once module file items are retrieved, use `download_course_file(file_id=..., destination_path=...)` to download them.

---

## 🧩 Tool Bundles & On-Demand Discovery

Canvas tools are split into compact sub-bundles to keep context windows lean:
- **`canvas`** (Default): `get_canvas_courses`, `get_canvas_assignments`, `get_upcoming_assignments`, `get_assignment_details`, `read_course_file`, `get_course_syllabus`
- **`canvas_modules`**: `get_course_modules`, `get_module_items`, `search_module_items`, `get_course_pages`, `read_course_page`
- **`canvas_files`**: `get_course_files`, `download_course_file`
- **`canvas_grades`**: `get_course_grades`, `get_recent_grades`, `get_canvas_dashboard_todos`
- **`canvas_discussions`**: `get_course_discussions`, `read_discussion_topic`
- **`canvas_quizzes`**: `get_course_quizzes`

### How to Get Missing Tools:
If you need module or file tools and they are not currently in your active tool list, **do not guess or say you lack the tool**. Simply call:
- `search_for_tool(query="modules", category="canvas")` -> immediately injects the `canvas_modules` bundle.
- `search_for_tool(query="download files", category="canvas")` -> immediately injects the `canvas_files` bundle.

---

## ⚠️ Anti-Patterns to AVOID
1. **DO NOT guess numeric IDs** like `course_id=1` or `assignment_id=1`. Pass the course title and assignment title as strings; fuzzy matching will resolve the real IDs.
2. **DO NOT cycle through `search_module_items`** when looking for homework or assignment requirements. Use `get_assignment_details`.
3. **DO NOT query assignments when the user asks for lectures or slides.**
4. **DO NOT fail when `get_course_files` returns 403 Forbidden.** Pivot to `search_module_items` / `get_course_modules`.
5. **DO NOT attempt submissions**. Canvas tools are strictly read-only.
