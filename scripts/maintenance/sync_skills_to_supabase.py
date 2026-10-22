"""
sync_skills_to_supabase.py

Maintenance script: scans src/skills/**/SKILL.md files, parses their frontmatter,
and upserts the skill index into Supabase's `skills` table.

Run this after:
  - Adding a new SKILL.md file
  - Updating skill metadata (name, description, tags, etc.)
  - Restoring from a fresh clone (to populate the Supabase index)

Usage:
    python scripts/maintenance/sync_skills_to_supabase.py
"""

import sys
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

SKILLS_DIR = PROJECT_ROOT / "src" / "skills"


def parse_frontmatter(skill_file: Path) -> Optional[Dict[str, Any]]:
    """Parse YAML frontmatter from a SKILL.md file."""
    content = skill_file.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        print(f"  ⚠ No frontmatter found: {skill_file}")
        return None

    frontmatter_text = match.group(1)
    data: Dict[str, Any] = {}

    for line in frontmatter_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip().strip('"')

    name = data.get("name")
    description = data.get("description", "")
    if not name or not description:
        print(f"  ✗ Missing name or description: {skill_file}")
        return None

    # Relative path from project root (use forward slashes for cross-platform)
    rel_path = str(skill_file.relative_to(PROJECT_ROOT)).replace("\\", "/")

    # Parse tags
    tags_raw = data.get("tags", "")
    tags = [t.strip().strip("[]").strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    # Version
    version = data.get("version", "1.0.0")

    # Subagent link
    subagent = data.get("subagent", None)
    if subagent and subagent.lower() in ("none", "null", ""):
        subagent = None

    # Requires
    requires = data.get("requires", None)
    if requires and requires.lower() in ("none", "null", ""):
        requires = None

    # Enabled flag
    enabled_raw = data.get("enabled", "true")
    enabled = enabled_raw.lower() != "false"

    return {
        "name": name,
        "description": description,
        "file_path": rel_path,
        "version": version,
        "tags": tags,
        "subagent": subagent,
        "requires": requires,
        "enabled": enabled,
    }


def sync_skills():
    """Scan all SKILL.md files and upsert them to Supabase."""
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        print("✗ SUPABASE_URL and SUPABASE_KEY must be set in .env")
        sys.exit(1)

    client = create_client(url, key)
    print(f"\n[ SCAN ] Scanning: {SKILLS_DIR}\n")

    skill_files = list(SKILLS_DIR.rglob("SKILL.md"))
    if not skill_files:
        print("[ WARN ] No SKILL.md files found.")
        return

    print(f"Found {len(skill_files)} skill(s):\n")

    success = 0
    failed = 0

    for skill_file in sorted(skill_files):
        print(f"  * {skill_file.relative_to(PROJECT_ROOT)}")
        data = parse_frontmatter(skill_file)
        if not data:
            failed += 1
            continue

        try:
            # Upsert: insert or update by name
            result = (
                client.table("skills")
                .upsert(data, on_conflict="name")
                .execute()
            )
            if result.data:
                print(f"     + Synced: {data['name']} (v{data['version']})")
                success += 1
            else:
                print(f"     - Supabase returned no data for: {data['name']}")
                failed += 1
        except Exception as e:
            print(f"     - Error syncing {data['name']}: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Results - Synced: {success} | Failed: {failed}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    sync_skills()
