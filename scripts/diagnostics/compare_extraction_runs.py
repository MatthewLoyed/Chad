"""Compare tier-1 vs full-pipeline benchmark JSON artifacts."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def tier_bucket(source: str | None) -> str:
    s = source or "none"
    if s == "search_snippet":
        return "Tier 3: snippet"
    if s.startswith("playwright"):
        return "Tier 2: Playwright"
    if s.startswith("fetch_html") or s in {"trafilatura_precision", "trafilatura_recall", "readability_markdown"}:
        return "Tier 1: fetch_html"
    if s == "none":
        return "FAILED"
    return f"other ({s})"


def main() -> None:
    base = Path(__file__).resolve().parents[2] / "artifacts"
    t1_path = base / "web_extraction_tier1.json"
    fp_path = base / "web_extraction_full_pipeline.json"
    if len(sys.argv) >= 3:
        t1_path, fp_path = Path(sys.argv[1]), Path(sys.argv[2])

    t1 = json.loads(t1_path.read_text(encoding="utf-8"))
    fp = json.loads(fp_path.read_text(encoding="utf-8"))

    print("=== PHASE COMPARISON (52 sites) ===\n")
    for label, data in [
        ("Tier-1 bench (httpx + extract + snippet)", t1),
        ("Full pipeline (fetch_html + Playwright + snippet)", fp),
    ]:
        s = data["summary"]
        cases = s["cases"]
        print(label)
        print(f"  page_extract_ok: {s['page_extract_ok']}/{cases} ({100*s['page_extract_ok']/cases:.0f}%)")
        print(f"  final_usable:    {s['final_usable']}/{cases} ({100*s['final_usable']/cases:.0f}%)")
        print(f"  total_score:     {s['total_final_score']:.0f}\n")

    print("Full pipeline — winning tier per URL:")
    buckets = Counter(tier_bucket(r["final_source"]) for r in fp["results"])
    for name, count in sorted(buckets.items(), key=lambda x: -x[1]):
        print(f"  {name:28s} {count}")

    print("\nPlaywright/content upgrades vs tier-1 bench (>5 score delta):")
    t1_by_id = {r["id"]: r for r in t1["results"]}
    upgrades = []
    for r in fp["results"]:
        prev = t1_by_id[r["id"]]
        delta = r["final_score"] - prev["final_score"]
        if delta > 5:
            upgrades.append((delta, r["id"], r["category"], prev["final_score"], r["final_score"], r["final_source"]))
    upgrades.sort(reverse=True)
    print(f"  {len(upgrades)} sites improved")
    for delta, rid, cat, old, new, src in upgrades[:20]:
        print(f"    {rid:22s} {cat:16s} {old:5.0f} -> {new:5.0f}  (+{delta:.0f})  {src}")

    fails = [r for r in fp["results"] if not r["final_ok"]]
    print(f"\nFailures (full pipeline): {len(fails)}")
    for r in fails:
        print(f"  {r['id']}: {r.get('note')}")


if __name__ == "__main__":
    main()
