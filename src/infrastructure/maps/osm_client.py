"""
Async OpenStreetMap client — Nominatim + Overpass + OSRM.

Free public endpoints; no API keys. Self-throttles Nominatim (~1 req/s) per
https://operations.osmfoundation.org/policies/nominatim/
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

USER_AGENT = "ChadAssistant/1.0 (osm-maps; local personal assistant)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSRM_URL = "https://router.project-osrm.org"

# Amenity values accepted by search_places (Overpass amenity=* or tourism/shop tags).
AMENITY_ALIASES: Dict[str, tuple[str, str]] = {
    "restaurant": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "coffee": ("amenity", "cafe"),
    "bar": ("amenity", "bar"),
    "pub": ("amenity", "pub"),
    "fast_food": ("amenity", "fast_food"),
    "pharmacy": ("amenity", "pharmacy"),
    "hospital": ("amenity", "hospital"),
    "clinic": ("amenity", "clinic"),
    "bank": ("amenity", "bank"),
    "atm": ("amenity", "atm"),
    "fuel": ("amenity", "fuel"),
    "parking": ("amenity", "parking"),
    "school": ("amenity", "school"),
    "university": ("amenity", "university"),
    "library": ("amenity", "library"),
    "police": ("amenity", "police"),
    "toilets": ("amenity", "toilets"),
    "hotel": ("tourism", "hotel"),
    "attraction": ("tourism", "attraction"),
    "museum": ("tourism", "museum"),
    "park": ("leisure", "park"),
    "supermarket": ("shop", "supermarket"),
    "convenience": ("shop", "convenience"),
}

OSRM_PROFILES = {
    "driving": "driving",
    "drive": "driving",
    "car": "driving",
    "walking": "foot",
    "walk": "foot",
    "foot": "foot",
    "cycling": "bike",
    "bike": "bike",
    "bicycle": "bike",
}


class OsmClient:
    """Thin async client over public OSM services."""

    def __init__(
        self,
        *,
        nominatim_url: str = NOMINATIM_URL,
        overpass_url: str = OVERPASS_URL,
        osrm_url: str = OSRM_URL,
        nominatim_min_interval: float = 1.05,
        timeout: float = 30.0,
    ) -> None:
        self._nominatim_url = nominatim_url.rstrip("/")
        self._overpass_url = overpass_url
        self._osrm_url = osrm_url.rstrip("/")
        self._nominatim_min_interval = nominatim_min_interval
        self._timeout = timeout
        self._last_nominatim_at = 0.0
        self._nominatim_lock = asyncio.Lock()
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async def _throttle_nominatim(self) -> None:
        async with self._nominatim_lock:
            elapsed = time.monotonic() - self._last_nominatim_at
            wait = self._nominatim_min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_nominatim_at = time.monotonic()

    async def geocode(
        self,
        query: str,
        *,
        limit: int = 5,
        country_codes: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Forward geocode an address or place name."""
        q = (query or "").strip()
        if not q:
            return []
        await self._throttle_nominatim()
        params: Dict[str, Any] = {
            "q": q,
            "format": "json",
            "addressdetails": 1,
            "limit": max(1, min(int(limit), 10)),
        }
        if country_codes:
            params["countrycodes"] = country_codes
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.get(f"{self._nominatim_url}/search", params=params)
            if resp.status_code == 429:
                raise OsmRateLimitError("Nominatim rate limit hit — wait a second and retry.")
            resp.raise_for_status()
            data = resp.json()
        return [_normalize_nominatim(item) for item in data if isinstance(item, dict)]

    async def reverse_geocode(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        await self._throttle_nominatim()
        params = {"lat": lat, "lon": lon, "format": "json", "addressdetails": 1}
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.get(f"{self._nominatim_url}/reverse", params=params)
            if resp.status_code == 429:
                raise OsmRateLimitError("Nominatim rate limit hit — wait a second and retry.")
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict) or data.get("error"):
            return None
        return _normalize_nominatim(data)

    async def search_places(
        self,
        *,
        lat: float,
        lon: float,
        amenity: Optional[str] = None,
        query: Optional[str] = None,
        radius_m: int = 1000,
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        """Find POIs near a point via Overpass (amenity preferred) or Nominatim bias."""
        radius = max(50, min(int(radius_m), 50000))
        lim = max(1, min(int(limit), 50))

        if amenity:
            tag_key, tag_val = _resolve_amenity(amenity)
            ql = (
                f"[out:json][timeout:25];"
                f"(nwr(around:{radius},{lat},{lon})[\"{tag_key}\"=\"{tag_val}\"];);"
                f"out center tags {lim};"
            )
            elements = await self._overpass(ql)
            places = [_normalize_overpass(el) for el in elements]
            places = [p for p in places if p.get("name")]
            return places[:lim]

        # Free-text: Nominatim with viewbox around the point
        if query:
            await self._throttle_nominatim()
            delta = radius / 111_320.0  # rough degrees
            viewbox = f"{lon - delta},{lat + delta},{lon + delta},{lat - delta}"
            params: Dict[str, Any] = {
                "q": query.strip(),
                "format": "json",
                "addressdetails": 1,
                "limit": lim,
                "viewbox": viewbox,
                "bounded": 1,
            }
            async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
                resp = await client.get(f"{self._nominatim_url}/search", params=params)
                if resp.status_code == 429:
                    raise OsmRateLimitError("Nominatim rate limit hit — wait a second and retry.")
                resp.raise_for_status()
                data = resp.json()
            return [_normalize_nominatim(item) for item in data if isinstance(item, dict)]

        raise ValueError("Provide amenity or query for search_places.")

    async def get_route(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        mode: str = "driving",
    ) -> Dict[str, Any]:
        profile = OSRM_PROFILES.get((mode or "driving").lower().strip(), "driving")
        coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
        url = f"{self._osrm_url}/route/v1/{profile}/{coords}"
        params = {"overview": "false", "steps": "true", "geometries": "geojson"}
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise OsmRoutingError(data.get("message") or "No route found.")
        route = data["routes"][0]
        legs = route.get("legs") or []
        steps: List[Dict[str, Any]] = []
        for leg in legs:
            for step in leg.get("steps") or []:
                man = step.get("maneuver") or {}
                steps.append({
                    "instruction": man.get("type") or "continue",
                    "modifier": man.get("modifier"),
                    "name": step.get("name") or "",
                    "distance_m": round(float(step.get("distance") or 0), 1),
                    "duration_s": round(float(step.get("duration") or 0), 1),
                })
        return {
            "distance_m": round(float(route.get("distance") or 0), 1),
            "distance_km": round(float(route.get("distance") or 0) / 1000.0, 2),
            "duration_s": round(float(route.get("duration") or 0), 1),
            "duration_min": round(float(route.get("duration") or 0) / 60.0, 1),
            "mode": profile,
            "steps": steps[:40],
            "step_count": len(steps),
        }

    async def _overpass(self, query: str) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.post(self._overpass_url, data={"data": query})
            if resp.status_code in (429, 504):
                raise OsmRateLimitError(
                    f"Overpass busy ({resp.status_code}) — retry in a few seconds."
                )
            resp.raise_for_status()
            data = resp.json()
        elements = data.get("elements") if isinstance(data, dict) else None
        return [e for e in (elements or []) if isinstance(e, dict)]


class OsmRateLimitError(RuntimeError):
    """Public OSM endpoint asked us to slow down."""


class OsmRoutingError(RuntimeError):
    """OSRM could not compute a route."""


_default_client: Optional[OsmClient] = None


def get_osm_client() -> OsmClient:
    global _default_client
    if _default_client is None:
        _default_client = OsmClient()
    return _default_client


def _resolve_amenity(amenity: str) -> tuple[str, str]:
    key = (amenity or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in AMENITY_ALIASES:
        return AMENITY_ALIASES[key]
    # Default: treat as amenity=* tag
    return ("amenity", key)


def _normalize_nominatim(item: Dict[str, Any]) -> Dict[str, Any]:
    lat = float(item.get("lat") or 0)
    lon = float(item.get("lon") or 0)
    osm_type = item.get("osm_type")
    osm_id = item.get("osm_id")
    osm_url = None
    if osm_type and osm_id:
        osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    name = (
        item.get("name")
        or address.get("amenity")
        or address.get("tourism")
        or address.get("shop")
        or (item.get("display_name") or "").split(",")[0]
        or "Unnamed"
    )
    return {
        "name": str(name).strip(),
        "display_name": item.get("display_name") or str(name),
        "lat": lat,
        "lon": lon,
        "category": item.get("type") or item.get("class") or "",
        "address": item.get("display_name") or "",
        "osm_type": osm_type,
        "osm_id": osm_id,
        "osm_url": osm_url,
        "importance": item.get("importance"),
    }


def _normalize_overpass(el: Dict[str, Any]) -> Dict[str, Any]:
    tags = el.get("tags") if isinstance(el.get("tags"), dict) else {}
    center = el.get("center") if isinstance(el.get("center"), dict) else {}
    lat = el.get("lat")
    lon = el.get("lon")
    if lat is None:
        lat = center.get("lat")
    if lon is None:
        lon = center.get("lon")
    lat_f = float(lat) if lat is not None else 0.0
    lon_f = float(lon) if lon is not None else 0.0
    osm_type = el.get("type")  # node/way/relation
    osm_id = el.get("id")
    osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else None
    category = (
        tags.get("amenity")
        or tags.get("tourism")
        or tags.get("shop")
        or tags.get("leisure")
        or tags.get("highway")
        or ""
    )
    name = tags.get("name") or tags.get("brand") or category or "Unnamed"
    addr_parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:city"),
    ]
    address = ", ".join(p for p in addr_parts if p) or tags.get("addr:full") or ""
    return {
        "name": str(name).strip(),
        "display_name": str(name).strip(),
        "lat": lat_f,
        "lon": lon_f,
        "category": str(category),
        "address": address,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "osm_url": osm_url,
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "website": tags.get("website") or tags.get("contact:website"),
        "opening_hours": tags.get("opening_hours"),
    }
