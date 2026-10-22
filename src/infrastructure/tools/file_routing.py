"""Shared Drive + local filesystem routing — keep supervisor and traversal aligned.

Known-target / quick lookups use atomic tools on the supervisor. Open-ended
explore / search-through (Drive or local FS) goes through traversal_agent once.
"""
from __future__ import annotations

FILE_ROUTING_SUPERVISOR = (
    "- **Known file/folder target** (Drive `file_id`/share URL from DOCUMENT CONTEXT or the "
    "user; local path from catalog or the user) → call `google_drive_read_file` or "
    "`read_file` yourself. Several known targets → parallel direct reads; do NOT spawn "
    "traversal just because N > 1.\n"
    "- **Quick specific search** (one named file/folder: search → optional list → read; or "
    "a project folder name under coding roots) → `google_drive_search` / "
    "`google_drive_list_folder` / `google_drive_read_file`, or `fuzzy_find_local` / "
    "`list_directory` / `read_file` / `find_project`, yourself.\n"
    "- **Explore / search through files** (unknown location, hop-by-hop browse, open-ended "
    "folder walk, multi-hop gather, reorganize, dig through a tree for matches — Drive or "
    "local) → `delegate_to_traversal_agent` ONCE. The `task` argument must be the full "
    "end-to-end retrieval (what to find, open, read, and return). If several pending plan "
    "steps are all explore/file-walk work, merge them into **one** delegation.\n"
)

TRAVERSAL_AGENT_SUPERVISOR_ROSTER = (
    "Explores Google Drive and the local filesystem hop-by-hop when the location is "
    "unknown or the ask needs open-ended search-through / multi-hop gather. Not for a "
    "known id/path, find_project hit, or a single quick named search you can do with "
    "google_drive_* / fuzzy_find_local / list_directory / read_file / find_project yourself."
)
