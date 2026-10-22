---
name: google_drive
description: "Google Drive folder and file search/read workflows. Native tools only."
version: 1.0.0
tags: [drive, docs, files]
---

## Drive workflow

1. **Named folder or file** → `google_drive_search`
   - Folder: `file_type: "folder"`
   - Doc: `file_type: "document"` (or omit for any type)
2. **Browse a known folder** → `google_drive_list_folder` with `folder_id` from step 1
   - Do not list `root` to hunt for names the user already gave
3. **Read content** → `google_drive_read_file` with `file_id` from search or list

## Rules
- Search before browse/read when the user names a target.
- Return at most 5 items unless the user asks for more.
- Use native `google_drive_*` tools only.
