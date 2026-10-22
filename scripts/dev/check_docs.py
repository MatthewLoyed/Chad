"""Offline documentation integrity and advisory duplication checks.

Only Git-tracked documentation is read. No application imports or network calls.
Archives are opt-in; duplicate prose is advisory, never a merge gate.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import os
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
ROSTER = Path("docs/architecture/HIERARCHICAL_AGENTS.md")
FACTORY = Path("src/core/orchestration/agents/factory.py")


def git_binary() -> str:
    """Support the default Windows install even in a reduced agent PATH."""
    found = shutil.which("git")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe"
    if os.name == "nt" and default.is_file():
        return str(default)
    raise RuntimeError("Git is required for tracked-file checks; add git to PATH")


def prose(text: str) -> str:
    """Remove fenced code, including examples containing Markdown syntax."""
    result, fence = [], None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            result.append(line)
    return "\n".join(result)


def anchors(text: str) -> set[str]:
    """GitHub-style ATX/setext heading IDs, including repeated headings."""
    found, counts = set(), defaultdict(int)
    lines = prose(text).splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        heading = match[1] if match else None
        if heading is None and index + 1 < len(lines) and line.strip():
            if re.fullmatch(r"\s{0,3}(?:=+|-+)\s*", lines[index + 1]):
                heading = line.strip()
        if heading is None:
            continue
        heading = re.sub(r"<[^>]*>", "", heading)
        heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        slug = "".join(c for c in heading.lower() if c in " -_" or
                       unicodedata.category(c)[0] in "LN")
        slug = slug.replace(" ", "-")
        count = counts[slug]
        found.add(f"{slug}-{count}" if count else slug)
        counts[slug] += 1
    found.update(re.findall(r'<a\s+(?:name|id)=["\']([^"\']+)', text))
    return found


def destinations(text: str) -> list[str]:
    """Inline/image and explicit reference links; code spans are not links."""
    text = re.sub(r"`+[^`\n]*`+", "", prose(text))
    definitions = dict(re.findall(r"^\s{0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?", text, re.M))
    definitions = {key.casefold(): value for key, value in definitions.items()}
    links = list(definitions.values())
    # Angle-quoted paths may contain spaces; unquoted destinations may contain
    # balanced parentheses (as in a filename). Optional link titles are skipped.
    for match in re.finditer(r"!?\[[^\]\n]*\]\(", text):
        tail = text[match.end():]
        if tail.startswith("<"):
            end = tail.find(">")
            if end >= 0:
                links.append(tail[1:end])
            continue
        depth, dest = 0, []
        for char in tail:
            if char == ")" and depth == 0 or char.isspace():
                break
            depth += (char == "(") - (char == ")")
            dest.append(char)
        if dest:
            links.append("".join(dest))
    for match in re.finditer(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]", text):
        key = (match[2] or match[1]).casefold()
        if key in definitions:
            links.append(definitions[key])
    return links


def link_defects(root: Path, source: Path) -> set[str]:
    defects = set()
    for dest in destinations((root / source).read_text(encoding="utf-8")):
        parts = urlsplit(dest)
        if parts.scheme or parts.netloc:
            continue  # External URLs are intentionally not crawled.
        path = unquote(parts.path).replace("\\", "/")
        target = (root / path.lstrip("/")) if path.startswith("/") else root / source.parent / path
        if not path:
            target = root / source
        target = target.resolve()
        if not target.is_relative_to(root.resolve()):
            defects.add(f"{source.as_posix()}: outside repository: {dest}")
        elif not target.exists():
            defects.add(f"{source.as_posix()}: missing path: {dest}")
        elif parts.fragment and target.suffix.lower() == ".md":
            if unquote(parts.fragment) not in anchors(target.read_text(encoding="utf-8")):
                defects.add(f"{source.as_posix()}: missing heading: {dest}")
    return defects


def roster_block(root: Path) -> str:
    tree = ast.parse((root / FACTORY).read_text(encoding="utf-8"))
    value = next(n.value for n in tree.body if isinstance(n, ast.Assign) and
                 any(isinstance(t, ast.Name) and t.id == "AGENT_CONFIGS" for t in n.targets))
    registry = ast.literal_eval(value)
    rows = ["| Registered specialist | Tool domain |", "|---|---|"]
    rows.extend(f"| `{name}` | `{config['domain']}` |" for name, config in registry.items())
    return "<!-- agent-roster:start -->\n" + "\n".join(rows) + "\n<!-- agent-roster:end -->"


def check_roster(root: Path, write: bool = False) -> set[str]:
    page = root / ROSTER
    text = page.read_text(encoding="utf-8")
    expected = roster_block(root)
    pattern = r"<!-- agent-roster:start -->.*?<!-- agent-roster:end -->"
    match = re.search(pattern, text, re.S)
    if not match:
        return {f"{ROSTER.as_posix()}: missing agent roster markers"}
    if write:
        page.write_text(text[:match.start()] + expected + text[match.end():], encoding="utf-8")
    elif match[0] != expected:
        return {f"{ROSTER.as_posix()}: stale agent roster; run --write-roster"}
    return set()


def tracked_docs(root: Path, archives: bool = False) -> list[Path]:
    names = subprocess.check_output([git_binary(), "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    return sorted(Path(name) for name in names if
                  (name.endswith(".md") or name == "CLAUDE.md.example") and
                  ("/" not in name or name.startswith(("docs/", "evals/"))) and
                  (archives or not name.startswith("docs/archive/")))


def duplicate_warnings(root: Path, pages: list[Path]) -> list[str]:
    blocks: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        if page.as_posix().startswith("docs/archive/"):
            continue
        for block in prose((root / page).read_text(encoding="utf-8")).split("\n\n"):
            normalized = " ".join(block.split())
            if len(normalized) >= 250 and not normalized.startswith(("#", "|", "<!--")):
                blocks[normalized].add(page.as_posix())
    return ["Repeated prose: " + ", ".join(sorted(files)) for files in blocks.values() if len(files) > 1]


def audit(root: Path, pages: list[Path]) -> set[str]:
    defects = set().union(*(link_defects(root, page) for page in pages))
    return defects | check_roster(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-roster", action="store_true")
    parser.add_argument("--archives", action="store_true", help="Also inspect historical archive links")
    args = parser.parse_args()
    if args.write_roster:
        errors = check_roster(ROOT, write=True)
        if errors:
            print("\n".join(sorted(errors)))
            return 1
    pages = tracked_docs(ROOT, archives=args.archives)
    defects = audit(ROOT, pages)
    for item in sorted(defects):
        print("ERROR " + item)
    warnings = duplicate_warnings(ROOT, pages)
    for warning in warnings[:10]:
        print("ADVISORY " + warning)
    active = [p for p in pages if not p.as_posix().startswith("docs/archive/")]
    print(f"Checked {len(pages)} pages; {len(defects)} defects; "
          f"{len(warnings)} duplicate prose groups (advisory).")
    print(f"Non-archive documentation: {len(active)} pages; "
          f"{sum(len((ROOT / p).read_text(encoding='utf-8')) for p in active)} characters. "
          f"AGENTS.md: {len((ROOT / 'AGENTS.md').read_text(encoding='utf-8').splitlines())} lines.")
    return int(bool(defects))


if __name__ == "__main__":
    raise SystemExit(main())
