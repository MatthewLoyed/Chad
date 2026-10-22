"""
Snapshot Pruner - Token optimization for browser accessibility trees.
Parses Playwright MCP snapshots and filters out non-interactive/redundant nodes.
"""

import re
import logging
from typing import Tuple

logger = logging.getLogger("SnapshotPruner")
logger.setLevel(logging.DEBUG)

def prune_snapshot(raw_text: str, max_chars: int = 35000) -> Tuple[str, int, int]:
    """
    Prunes a Playwright accessibility tree snapshot to keep only relevant nodes.
    
    Rules:
    1. Strip all `/url:` lines (these contain bloated 2.5KB ad/redirect tracking links).
    2. Keep lines where the role is interactive (button, link, etc.) or structural (heading, navigation).
    3. Drop generic and layout nodes (generic, none, group, row, cell, listitem, list) unless
       they carry a meaningful label or interactive cursor.
    4. Keep substantive text nodes (prices, descriptions, ratings).
    5. Enforce max_chars context budget to prevent flooding context window.
    
    Args:
        raw_text: The raw string content from the MCP tool response.
        max_chars: Maximum characters to retain in the pruned tree (default 35,000).
        
    Returns:
        A tuple of (pruned_text, original_line_count, kept_line_count).
    """
    if not isinstance(raw_text, str) or not raw_text:
        return "", 0, 0
    
    lines = raw_text.splitlines()
    original_line_count = len(lines)
    
    # Roles to keep explicitly (interactive + structural)
    ROLES_TO_KEEP = {
        "heading", "link", "button", "textbox", "searchbox", 
        "combobox", "checkbox", "radio", "menuitem", "tab", 
        "option", "navigation", "main", "form", "dialog", 
        "alert", "banner", "switch", "slider", "spinbutton"
    }
    
    # Generic layout containers to drop unless they carry a meaningful label or interactive cursor
    LAYOUT_ROLES_TO_DROP = {
        "generic", "none", "presentation", "separator", 
        "group", "rowgroup", "row", "cell", "div", "span", "listitem", "list"
    }
    
    pruned_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue

        # Rule 1: Strip out bloated tracking URLs (/url: lines)
        if stripped_line.startswith(("- /url:", "/url:", "- url:")):
            continue

        match = re.search(r"^- (\w+)(?:\s+\"([^\"]*)\")?", stripped_line)
        if match:
            role = match.group(1).lower()
            name = (match.group(2) or "").strip()
            # Playwright puts visible labels, codes and share links in generic
            # leaves too: `generic [ref=e1]: value`. Drop layout, not its text.
            if role in LAYOUT_ROLES_TO_DROP and re.search(r"(?:\]|\b" + role + r"):\s*\S", stripped_line):
                pruned_lines.append(line)
                continue
            # If it's a layout container without a name or interactive pointer, drop it
            if role in LAYOUT_ROLES_TO_DROP and not name and "cursor=pointer" not in line:
                continue
            # Drop empty links marked aria-hidden or without name
            if role == "link" and not name and "[aria-hidden]" in line:
                continue

        # Substantive text nodes (prices, descriptions, ratings) - always keep
        if stripped_line.startswith("- text:") or stripped_line.startswith("- paragraph:"):
            # Keep if it contains letters, digits, or price signs
            if re.search(r"[a-zA-Z0-9\$]", stripped_line):
                pruned_lines.append(line)
            continue

        # Check if it follows the tree item format
        if not stripped_line.startswith("- "):
            if stripped_line:
                pruned_lines.append(line)
            continue
            
        if match:
            role = match.group(1).lower()
            if role in ROLES_TO_KEEP:
                pruned_lines.append(line)
            elif role in LAYOUT_ROLES_TO_DROP:
                continue
            elif re.search(r"\[ref=\w+", line):
                pruned_lines.append(line)
            else:
                pruned_lines.append(line)
        elif re.search(r"\[ref=\w+", line):
            pruned_lines.append(line)
            
    kept_line_count = len(pruned_lines)
    pruned_text = "\n".join(pruned_lines)

    if max_chars and len(pruned_text) > max_chars:
        pruned_text = (
            pruned_text[:max_chars]
            + f"\n\n... [Snapshot truncated from {len(pruned_text)} to {max_chars} chars to fit context budget; scroll or refine query to view more] ..."
        )
    
    return pruned_text, original_line_count, kept_line_count

def render_set_of_mark(pruned_text: str, max_entries: int = 100) -> str:
    """
    Render a pruned Playwright accessibility tree as an explicit numbered
    Set-of-Mark (SoM) menu of the interactive elements.

    Filters out purely structural/layout nodes (table, cell, row, generic) so the
    model is presented ONLY with truly clickable, typable, selectable controls.
    Filters out unlabeled empty links and caps total entries to prevent context flooding.
    """
    if not isinstance(pruned_text, str) or not pruned_text:
        return ""

    INTERACTIVE_ROLES = {
        "button", "link", "textbox", "searchbox", "combobox",
        "checkbox", "radio", "menuitem", "tab", "option",
        "switch", "slider", "spinbutton", "treeitem"
    }

    # role + optional quoted name, ending in a [ref=...] handle
    line_re = re.compile(r'-\s+(\w+)(?:\s+"([^"]*)")?.*?\[ref=(\w+)')

    entries = []
    seen_refs = set()
    for line in pruned_text.splitlines():
        m = line_re.search(line)
        if not m:
            continue
        role, name, ref = m.group(1), (m.group(2) or "").strip(), m.group(3)
        if ref in seen_refs:
            continue
        role_lower = role.lower()
        is_interactive = (
            role_lower in INTERACTIVE_ROLES
            or "cursor=pointer" in line
            or "[expanded=" in line
            or "[checked=" in line
            or "[selected=" in line
        )
        if not is_interactive:
            continue
        # Drop empty links with no name (reduces menu noise by hundreds of elements)
        if role_lower == "link" and not name:
            continue
        seen_refs.add(ref)
        entries.append((role, name, ref))

    if not entries:
        return ""

    lines = ["### Interactive Elements (Set-of-Mark)",
             'Pick a target by its number, then pass its exact target="..." value to the '
             'browser tool (e.g. browser_click with target="e34"). Never write "ref=e34".',
             ""]
    for idx, (role, name, ref) in enumerate(entries[:max_entries], start=1):
        label = f'{role} "{name}"' if name else role
        lines.append(f'[{idx}] {label}  ->  target="{ref}"')

    if len(entries) > max_entries:
        lines.append(f"... [{len(entries) - max_entries} more interactive elements omitted for brevity] ...")

    return "\n".join(lines)


def prune_uia_tree(raw_text: str) -> Tuple[str, int, int]:
    """
    Prunes a Windows UIA tree snapshot (from windows-mcp) to keep only relevant nodes.
    
    Format:
    List of Interactive Elements:
    # id|window|control_type|name|coords|metadata
    0|Title|Button|Name|(x,y)|{...}
    
    Args:
        raw_text: The raw string content from the MCP tool response.
        
    Returns:
        A tuple of (pruned_text, original_line_count, kept_line_count).
    """
    if not isinstance(raw_text, str) or not raw_text:
        return "", 0, 0
    
    lines = raw_text.splitlines()
    original_line_count = len(lines)
    
    # Types to keep explicitly
    TYPES_TO_KEEP = {
        "button", "menu item", "tab item", "edit", "combo box", 
        "check box", "radio button", "list item", "tree item",
        "hyperlink", "menu", "tab", "header", "header item"
    }
    
    # Types to drop
    TYPES_TO_DROP = {
        "text", "image", "group", "pane", "window", "toolbar",
        "title bar", "status bar", "scroll bar", "thumb"
    }
    
    pruned_lines = []
    in_interactive_table = False
    header_count = 0
    
    for line in lines:
        stripped = line.strip()
        
        # Track section state
        if "List of Interactive Elements:" in line:
            in_interactive_table = True
            pruned_lines.append(line)
            continue
            
        if in_interactive_table:
            # Check for table header or separator
            if stripped.startswith("# id") or set(stripped) == {"-", " ", "|"}:
                pruned_lines.append(line)
                continue
                
            # Parse table row: id|window|control_type|name|coords|metadata
            parts = stripped.split("|")
            if len(parts) >= 4:
                control_type = parts[2].lower()
                name = parts[3].strip()
                
                # Rule: Keep if it's a known interactive type OR has a non-empty name
                # (Some generic containers might be interactive if they have names)
                if control_type in TYPES_TO_KEEP or (name and control_type not in TYPES_TO_DROP):
                    pruned_lines.append(line)
                    continue
                else:
                    # Drop uninteresting items
                    continue
            
            # If we hit an empty line or another section, we're done with the table
            if not stripped:
                in_interactive_table = False
                pruned_lines.append(line)
                continue

        # For non-table lines (summaries, headers), keep them if they are not empty
        if not in_interactive_table:
            # Keep summaries and headers
            if stripped:
                pruned_lines.append(line)
            else:
                # Limit empty lines
                if pruned_lines and pruned_lines[-1].strip():
                    pruned_lines.append(line)

    kept_line_count = len(pruned_lines)
    pruned_text = "\n".join(pruned_lines)
    
    return pruned_text, original_line_count, kept_line_count
