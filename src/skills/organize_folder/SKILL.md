---
name: organize_folder
description: "Organizes a local folder by categorizing and moving files into logical subfolders. Use when the user asks to clean up, sort, or organize a directory. Supports custom rules and dry-run preview."
version: 1.0.0
tags: [filesystem, organization, files, productivity]
subagent: organize_folder_agent
---

## What This Skill Does

Analyzes a local directory's contents and reorganizes files into logical subfolders based on file type, date, or custom rules. Supports a **dry-run** preview mode before making any changes.

## Tools Required
- `list_directory` — scan folder contents
- `read_file` — inspect file metadata if needed
- `write_to_file` / `move_file` — execute the reorganization
- `execute_command` — for bulk move operations if needed

## Standard Operating Procedure (SOP)

### Step 1 — Scan
```
list_directory(path=target_folder, recursive=False)
```
Collect: file names, extensions, sizes, modification dates.

### Step 2 — Classify
Group files by a classification strategy:

**By type (default):**
| Category | Extensions |
|----------|------------|
| Documents | .pdf, .doc, .docx, .txt, .md |
| Images | .jpg, .jpeg, .png, .gif, .webp, .svg |
| Videos | .mp4, .mkv, .mov, .avi |
| Audio | .mp3, .wav, .flac, .m4a |
| Code | .py, .js, .ts, .html, .css, .json |
| Archives | .zip, .rar, .7z, .tar, .gz |
| Spreadsheets | .xlsx, .xls, .csv |
| Misc | everything else |

**By date (if requested):**
Folders like `2024-Q1`, `2024-Q2`, etc.

**Custom rules (if user specifies):**
Honor any explicit categorization rules provided.

### Step 3 — Preview (ALWAYS do this first)
Present the reorganization plan:
```
📁 Documents/   → 12 files
📁 Images/      → 34 files
📁 Code/        → 8 files
📁 Misc/        → 3 files
```
Ask: "Shall I proceed, or would you like to adjust any categories?"

### Step 4 — Execute
Only after user confirms:
- Create subdirectories as needed
- Move files to their classified location
- Report: "Done. Moved X files into Y categories."

### Step 5 — Report
Provide a summary:
- Files moved per category
- Any files that couldn't be moved (permission errors, etc.)
- Any ambiguous files left in place

## Critical Rules
- **NEVER** delete files — only move them
- **ALWAYS** show the preview before executing
- If a file already exists in the target folder, append `_1`, `_2`, etc. (do NOT overwrite)
- Skip hidden files (starting with `.`) unless explicitly requested
- Respect the user's existing subfolder structure — don't flatten it

## Subagent Delegation
For large directories (>500 files) or complex rule sets, delegate to `organize_folder_agent`
which handles batch processing more efficiently.
