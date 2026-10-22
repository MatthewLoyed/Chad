"""
Smoke-test OSM Nominatim once (live network).

  python scripts/dev/smoke_osm_maps.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.maps.osm_client import get_osm_client
from src.tools.maps.osm_tools import ShowMapTool, map_display_payload


async def main() -> int:
    client = get_osm_client()
    print("Geocoding 'Eiffel Tower, Paris'...")
    places = await client.geocode("Eiffel Tower, Paris", limit=1)
    if not places:
        print("FAIL: no geocode results")
        return 1
    top = places[0]
    print(f"OK: {top['name']} @ {top['lat']}, {top['lon']}")

    print("Searching cafes nearby (Overpass)...")
    cafes = await client.search_places(
        lat=top["lat"], lon=top["lon"], amenity="cafe", radius_m=400, limit=5
    )
    print(f"OK: {len(cafes)} cafes")

    markers = cafes[:5] if cafes else [top]
    tool = ShowMapTool()
    shown = await tool.execute(title="Smoke map", places=markers)
    payload = map_display_payload(shown)
    if not payload.get("map_view"):
        print("FAIL: map_display_payload empty")
        return 1
    print(f"OK: map_view with {len(payload['map_view']['places'])} markers")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
