---
name: osm-maps
description: Find places and show interactive OpenStreetMap cards in chat via free Nominatim/Overpass/OSRM tools (geocode, search_places, get_route, show_map). Use for nearby restaurants/cafes/POIs, directions, and map markers — not for Google reviews, news research (web_search), or opening Maps in a browser tab.
license: MIT (skill) + ODbL (OpenStreetMap data)
requires: geocode, search_places, get_route, show_map
runtime: native (httpx → Nominatim / Overpass / OSRM)
install: none — no API key, no Docker sidecar
---

# OpenStreetMap Places & Maps

Free place search, routing, and an inline Leaflet map card. Data comes from OpenStreetMap community mapping — **not** Google Places. There are no star ratings, review text, or guaranteed "open now" fields.

## When to use

| Need | Use |
|------|-----|
| Find restaurants / cafes / pharmacies / parks near a place | **osm-maps** (`geocode` → `search_places` → `show_map`) |
| Directions / ETA between two places | **osm-maps** (`geocode` both → `get_route`; optional `show_map`) |
| Show several known coords on a map | **osm-maps** (`show_map`) |
| Reviews, news, "best pizza according to blogs" | `web_search` (then map only if user wants locations plotted) |
| Just open Google Maps in a browser tab | Avoid — prefer the inline `show_map` card |

## Prerequisites

None. Tools are always-on native tools. Public OSM endpoints have fair-use rate limits (Nominatim ~1 req/s).

## Agent workflow (mandatory)

1. `load_skill('osm-maps')` once per places/maps task.
2. If the user gave a place name (not lat/lon), call `geocode(query=...)` first. Use the top result's `lat`/`lon`.
3. Call `search_places(lat=..., lon=..., amenity=..., radius_m=...)` — prefer **amenity** tags (`restaurant`, `cafe`, `pharmacy`, `park`, `hotel`, `supermarket`, `bank`, `atm`, `fuel`, `parking`, …). Use `query` only when amenity is too coarse (e.g. `"pizza"`).
4. Summarize top results in chat (name, category, address if present). Say clearly this is OpenStreetMap data — **do not invent ratings**.
5. Always finish a multi-place ask with `show_map(title=..., places=[...])` using **real** coordinates from tool results. Never invent lat/lon.
6. For directions: geocode origin and destination → `get_route` → optionally `show_map` with origin + dest markers.

## Tool cheat sheet

| Tool | Role |
|------|------|
| `geocode` | Place/address → lat/lon |
| `search_places` | Nearby POIs (amenity or free-text) |
| `get_route` | Driving / walking / cycling via OSRM |
| `show_map` | Inline interactive map card (markers + popups) |

## Gotchas

- Nominatim ~1 request/second — don't spam geocode in a loop.
- Overpass can be slow or empty in sparse areas; widen `radius_m` or try a different amenity before claiming "nothing exists."
- Public OSRM is best-effort (no live traffic).
- Hours / phone / website appear only when OSM tags have them — often missing.
- Never promise Google-quality ranking, photos, or reviews.

## Do not

- Dump raw JSON at the user.
- Skip `show_map` after a multi-place search.
- Open Google Maps in the browser when the inline card answers the ask.
- Claim paid-Maps quality or invent ratings/hours.
- Invent coordinates — only pass values returned by `geocode` / `search_places`.

## Reference

- Chad: `docs/features/VISUAL_OUTPUT.md` · `src/infrastructure/maps/osm_client.py`
- Data © OpenStreetMap contributors (ODbL)
