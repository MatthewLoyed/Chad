"""Numbered local-file pages that fit within the tool-message budget."""

import json
import re
from pathlib import Path
from typing import Any

from src.core.tools import failure, limits


# Space for the tool-data prefix and a repetition hint appended after dispatch.
_TOOL_MESSAGE_OVERHEAD = len("[TOOL_DATA]\n") + 384


def _encoded_length(text: str) -> int:
    return len(json.dumps(text)) - 2  # Exclude the surrounding JSON quotes.


def _matching_position(lines: list[str], query: str, offset: int, column: int) -> tuple[int, int] | None:
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    for index in range(offset - 1, len(lines)):
        start = column - 1 if index == offset - 1 else 0
        match = pattern.search(lines[index], start)
        if match:
            return index + 1, match.start() + 1
    return None


def _fitting_prefix(text: str, budget: int) -> int:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _encoded_length(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


def _numbered_lines(
    lines: list[str], offset: int, limit: int, column: int, budget: int,
) -> tuple[str, tuple[int, int] | None]:
    rendered = []
    end = min(offset + limit, len(lines) + 1)
    for number in range(offset, end):
        start = column - 1 if number == offset else 0
        text = lines[number - 1][start:]
        prefix = f"{number:4d} | "
        row = prefix + text
        cost = _encoded_length(row) + (_encoded_length("\n") if rendered else 0)
        if cost <= budget:
            rendered.append(row)
            budget -= cost
            continue
        if rendered:
            return "\n".join(rendered), (number, start + 1)
        # A single oversized line must still advance the cursor.
        take = _fitting_prefix(text, budget - _encoded_length(prefix))
        return (prefix + text[:take] if take else ""), (number, start + take + 1)
    return "\n".join(rendered), (end, 1) if end <= len(lines) else None


def file_page(
    text: str, *, file_path: Path, size_bytes: int, offset: int, limit: int,
    query: str, column: int,
) -> dict[str, Any]:
    # read_text normalizes CRLF and CR. Only split physical file lines, so a
    # Unicode separator embedded in a line cannot shift the cursor.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    total = len(lines)
    page = {
        "success": True,
        "result": f"Read from '{file_path.name}'",
        "file_name": file_path.name,
        "file_path": str(file_path),
        "size_bytes": size_bytes,
        "offset": offset,
        "column": column,
        "limit": limit,
        "total_lines": total,
        "content": "",
        "truncated": False,
        "has_more": False,
        "_trusted_source": True,
    }
    if not total:
        page["result"] = "File is empty"
        page["full_content"] = ""
        return page
    if offset > total:
        page["result"] = "Offset is beyond the end of the file"
        return page
    if column > len(lines[offset - 1]) + 1:
        return failure.failure(
            "Column is beyond the end of the starting line",
            failure_class=failure.BAD_INPUT,
            tool_name="read_file",
            next_actions=["Use column 1 or a continuation cursor returned by read_file."],
        )

    match = _matching_position(lines, query, offset, column) if query else None
    if query:
        page["matched_line"], page["matched_column"] = match or (None, None)
        if match is None:
            page["result"] = "Query not found. Reading from the requested offset."

    # Reserve metadata and the tool-data prefix used by orchestration. Use the
    # longest cursor values when calculating the remaining space for content.
    cap = int(limits.get("tool_output_max_chars")) - _TOOL_MESSAGE_OVERHEAD
    reserved = {
        **page, "offset": total, "column": len(text) + 1,
        "next_offset": total + 1, "next_column": len(text) + 1,
    }
    budget = cap - len(json.dumps(reserved))
    if match:
        offset, match_column = match
        # A match far into a long line would be outside its first page.
        row = f"{offset:4d} | {lines[offset - 1]}"
        column = match_column if _encoded_length(row) > budget else 1
        page.update(offset=offset, column=column)
    body, cursor = _numbered_lines(lines, offset, limit, column, budget)
    if not body:
        return failure.failure(
            "The tool output budget is too small for a file page",
            failure_class=failure.CONFIG,
            tool_name="read_file",
            next_actions=["Increase tools.tool_output_max_chars before retrying."],
        )

    page["content"] = body
    page["has_more"] = page["truncated"] = cursor is not None
    if cursor is not None:
        page["next_offset"], page["next_column"] = cursor
    elif offset == column == 1:
        # Keep the legacy raw field for complete reads when both views fit.
        full_page = {**page, "full_content": text}
        if len(json.dumps(full_page)) <= cap:
            return full_page
    return page
