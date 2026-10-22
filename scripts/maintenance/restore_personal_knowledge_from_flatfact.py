"""Rebuild gitignored config/personal_knowledge.md from a flat-fact backup.

Default backup: data/memories_flatfact_backup_*.json (Supabase → local export).
Writes under CHAD_HOME / project root, deletes knowledge_graph.json so GraphRAG
re-seeds on next init. Does NOT touch the tracked .example template.

Usage:
  set CHAD_HOME=<main checkout>
  set PYTHONPATH=.
  python scripts/maintenance/restore_personal_knowledge_from_flatfact.py
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def _project_root() -> Path:
    import os

    home = os.environ.get("CHAD_HOME")
    if home:
        return Path(home).resolve()
    return Path(__file__).resolve().parents[2]


def _is_placeholder(text: str) -> bool:
    s = (text or "").strip()
    if not s.startswith("["):
        return False
    low = s.lower()
    return any(
        k in low
        for k in ("fill in", "describe your", "your name", "any health", "short-term")
    )


def _pick_backup(data_dir: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = data_dir / p
        if not p.exists():
            raise SystemExit(f"Backup not found: {p}")
        return p
    cands = sorted(data_dir.glob("memories_flatfact_backup_*.json"))
    if not cands:
        raise SystemExit(f"No memories_flatfact_backup_*.json under {data_dir}")
    return cands[-1]


def _group_facts(rows: list[dict]) -> dict[str, list[str]]:
    by_cat: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content or content in seen:
            continue
        seen.add(content)
        cat = (row.get("category") or "general").strip().lower() or "general"
        by_cat[cat].append(content)
    return by_cat


def _section(title: str, lines: list[str]) -> str:
    body = "\n".join(lines).strip()
    if not body:
        return ""
    return f"## {title}\n\n{body}\n"


def build_markdown(by_cat: dict[str, list[str]], extras: list[tuple[str, str]]) -> str:
    """Map flat-fact categories into personal_knowledge.md sections."""
    # extras: (topic, content) from existing graph runtime nodes
    for topic, content in extras:
        if _is_placeholder(content):
            continue
        key = (topic or "general").lower()
        if content not in by_cat.get(key, []) and content not in sum(by_cat.values(), []):
            by_cat[key].append(content)

    identity = []
    identity.extend(by_cat.get("identity", []))
    identity.extend(by_cat.get("contact", []))
    identity.extend(by_cat.get("location", []))

    family = []
    family.extend(by_cat.get("family", []))
    family.extend(by_cat.get("relationship", []))

    # Prefer a readable family blurb first if we can compose one
    parts = []
    header = (
        "# Chad's Personal Knowledge Base\n\n"
        "Local source of truth for who you are. Gitignored — never commit this file.\n"
        "Open-source clones use `personal_knowledge.md.example` as the template.\n\n"
        "Each `##` section becomes one fact node in the knowledge graph.\n\n"
        "---\n"
    )
    parts.append(header)
    parts.append(_section("identity", identity))
    parts.append(_section("family", family))
    parts.append(_section("health", by_cat.get("health", []) + [
        c for c in by_cat.get("fact", []) if "dietary" in c.lower() or "food" in c.lower()
    ]))
    career_bits = [c for c in by_cat.get("fact", []) if "uplink" in c.lower() or "outlook" in c.lower() or "canvas" in c.lower()]
    parts.append(_section("career", by_cat.get("career", []) + career_bits))
    parts.append(_section("goals", by_cat.get("goals", [])))
    parts.append(_section("finances", by_cat.get("finances", []) + by_cat.get("budget", [])))
    parts.append(_section("hardware", by_cat.get("hardware", []) + [
        c for c in by_cat.get("fact", []) if "hardware" in c.lower() or "audio" in c.lower() or "windows 11 |" in c.lower()
    ]))
    prefs = by_cat.get("preference", []) + [
        c for c in by_cat.get("fact", [])
        if c not in career_bits and "hardware" not in c.lower() and "audio" not in c.lower()
        and "dietary" not in c.lower() and "food" not in c.lower() and "windows 11 |" not in c.lower()
    ]
    parts.append(_section("interests", prefs))
    parts.append(_section("projects", by_cat.get("projects", []) + by_cat.get("work", [])))
    parts.append(_section("routine", by_cat.get("routine", [])))
    # Catch-all remaining facts
    used = set()
    for block in parts:
        for line in block.splitlines():
            used.add(line.strip())
    leftover = []
    for cat, items in by_cat.items():
        if cat in ("identity", "contact", "location", "family", "relationship", "health",
                   "career", "goals", "finances", "budget", "hardware", "preference",
                   "projects", "work", "routine"):
            continue
        for item in items:
            if item.strip() not in used:
                leftover.append(item)
    for item in by_cat.get("fact", []):
        if item.strip() not in used and item not in leftover:
            leftover.append(item)
    parts.append(_section("general", leftover))
    return "\n".join(p for p in parts if p).rstrip() + "\n"


def _graph_extras(graph_path: Path) -> list[tuple[str, str]]:
    if not graph_path.exists():
        return []
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for node in data.get("nodes") or []:
        content = (node.get("content") or "").strip()
        if not content or _is_placeholder(content):
            continue
        if node.get("source") == "seed":
            # skip template seeds
            if _is_placeholder(content):
                continue
        topic = (node.get("topic") or "general").strip()
        out.append((topic, content))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backup", default=None, help="Path to flatfact JSON backup")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = _project_root()
    data_dir = root / "data"
    cfg = root / "config" / "personal_knowledge.md"
    example = root / "config" / "personal_knowledge.md.example"
    graph_path = data_dir / "knowledge_graph.json"

    backup = _pick_backup(data_dir, args.backup)
    rows = json.loads(backup.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("Backup must be a JSON list of memory rows")

    by_cat = _group_facts(rows)
    md = build_markdown(by_cat, _graph_extras(graph_path))

    print(f"root={root}")
    print(f"backup={backup} ({len(rows)} rows)")
    print(f"sections≈{len(re.findall(r'^## ', md, flags=re.M))}")
    print(f"bytes={len(md.encode('utf-8'))}")
    if example.exists() and example.read_text(encoding="utf-8") == md:
        raise SystemExit("Refusing to write — output identical to .example template")

    if args.dry_run:
        print("--- preview (first 1200 chars) ---")
        print(md[:1200])
        return 0

    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(md, encoding="utf-8", newline="\n")
    print(f"wrote {cfg}")

    if graph_path.exists():
        graph_path.unlink()
        print(f"deleted {graph_path} (will re-seed on next GraphRAG init)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
