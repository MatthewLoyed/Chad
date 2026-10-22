"""Live integration test for native Google Drive tools."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src.tools.workspace.drive import (
    GoogleDriveListFolderTool,
    GoogleDriveSearchTool,
    GoogleDriveReadFileTool,
    GoogleDriveCreateDocTool,
    GoogleDriveEditDocTool,
)


async def run(label: str, coro) -> dict:
    print(f"\n--- {label} ---")
    result = await coro
    success = result.get("success")
    print(f"success={success}")
    if success:
        for key in ("result", "folder_id", "query", "doc_id", "link", "name", "truncated"):
            if key in result:
                print(f"  {key}: {result[key]}")
        if "items" in result:
            print(f"  items: {len(result['items'])}")
            for item in result["items"][:3]:
                print(f"    - [{item.get('type')}] {item.get('name')} ({item.get('id')})")
        if "files" in result:
            print(f"  files: {len(result['files'])}")
            for item in result["files"][:3]:
                print(f"    - [{item.get('type')}] {item.get('name')} ({item.get('id')})")
        if "content" in result:
            preview = (result["content"] or "")[:120].replace("\n", " ")
            print(f"  content preview: {preview!r}...")
    else:
        print(f"  error: {result.get('error')}")
    return result


async def main() -> int:
    list_tool = GoogleDriveListFolderTool()
    search_tool = GoogleDriveSearchTool()
    read_tool = GoogleDriveReadFileTool()
    create_tool = GoogleDriveCreateDocTool()
    edit_tool = GoogleDriveEditDocTool()

    ok = True
    created_doc_id = None

    r1 = await run("google_drive_list_folder (root)", list_tool.execute(folder_id="root", limit=5))
    ok &= r1.get("success", False)

    # Search for any doc-like item; fall back to broad query
    r2 = await run(
        "google_drive_search",
        search_tool.execute(query="doc", file_type="all", limit=5),
    )
    ok &= r2.get("success", False)

    read_target = None
    for item in r2.get("files", []):
        if item.get("type") == "file" and item.get("mime_type", "").endswith(".document"):
            read_target = item
            break
    if not read_target:
        for item in r2.get("files", []):
            if item.get("type") == "file":
                read_target = item
                break

    if read_target:
        r3 = await run(
            "google_drive_read_file",
            read_tool.execute(file_id=read_target["id"], max_chars=500),
        )
        ok &= r3.get("success", False)
    else:
        print("\n--- google_drive_read_file ---")
        print("SKIP: no readable file found from search; will read newly created doc instead")
        ok = False  # tentatively fail until create test provides an id

    suffix = uuid.uuid4().hex[:8]
    title = f"Chad Drive Tool Test {suffix}"
    r4 = await run(
        "google_drive_create_doc",
        create_tool.execute(title=title, content="Initial line from create test.\n"),
    )
    ok &= r4.get("success", False)
    created_doc_id = r4.get("doc_id")

    if created_doc_id:
        if not read_target:
            r3b = await run(
                "google_drive_read_file (created doc)",
                read_tool.execute(file_id=created_doc_id, max_chars=500),
            )
            ok &= r3b.get("success", False)

        r5 = await run(
            "google_drive_edit_doc (append)",
            edit_tool.execute(doc_id=created_doc_id, append_text="\nAppended by edit test."),
        )
        ok &= r5.get("success", False)

        r6 = await run(
            "google_drive_edit_doc (find/replace)",
            edit_tool.execute(
                doc_id=created_doc_id,
                find_text="Initial line",
                replace_text="Updated line",
            ),
        )
        ok &= r6.get("success", False)

        r7 = await run(
            "google_drive_read_file (after edits)",
            read_tool.execute(file_id=created_doc_id, max_chars=500),
        )
        ok &= r7.get("success", False)
        if r7.get("success"):
            content = r7.get("content", "")
            if "Updated line" not in content or "Appended by edit test" not in content:
                print("  FAIL: edited content missing expected strings")
                ok = False

    print("\n" + "=" * 50)
    if created_doc_id:
        print(f"Created test doc (safe to delete): {r4.get('link')}")
    print("Overall:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
