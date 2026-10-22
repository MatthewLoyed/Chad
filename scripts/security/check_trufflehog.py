"""Fail on untriaged scanner candidates without printing credential values."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Exact reviewed public examples, not whole-file or detector exclusions.
# The GitHub candidate is Electron's public node-gyp commit SHA in its lockfile.
REVIEWED = {
    ("Github", "chad-hud/package-lock.json", "b5a15e6db74e90c6ee5294ee33ac2bd0fbc3c63990d7edb3d151d76f5a0f3fec"),
    ("Postgres", ".env.example", "fddeff9b48fdb29cef461a75ec1f64cbfe1649d9ef1d19c39ec24698012e51c1"),
    ("Postgres", "tests/unit/test_supabase_bootstrap_403.py", "b74f5dc4acda7726aa7b3d1fdc8b500f9d722b938f3ca58c0667789d4277000e"),
}


def reviewed(row: dict) -> bool:
    if row.get("Verified"):
        return False
    sources = row.get("SourceMetadata", {}).get("Data", {})
    source = sources.get("Git") or sources.get("Filesystem") or {}
    name = source.get("file", "").replace("\\", "/").removeprefix("./")
    digest = hashlib.sha256(row.get("Raw", "").encode()).hexdigest()
    return (row.get("DetectorName"), name, digest) in REVIEWED


def main(path: str) -> int:
    total = blocked = 0
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "DetectorName" not in row:
            if row.get("level") in {"error", "fatal"}:
                raise RuntimeError("Scanner reported an error; inspect its private log")
            continue
        total += 1
        if not reviewed(row):
            blocked += 1
            print("Unreviewed candidate: " + str(row.get("DetectorName")))
    print(f"TruffleHog candidates: {total}; exact reviewed examples: {total - blocked}; blocked: {blocked}")
    return int(blocked > 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
