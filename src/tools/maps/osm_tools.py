"""Native OSM place/route tools — free, no API key."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.tools.formatter import tool_display
from src.infrastructure.maps.osm_client import (
    OsmRateLimitError,
    OsmRoutingError,
    get_osm_client,
)
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label


def _err(exc: Exception) -> Dict[str, Any]:
    return {"success": False, "error": str(exc)}


@tool_display('Geocoding "{query}"...')
class GeocodeTool(BaseTool):
    name = "geocode"
    description = (
        "Convert a place name or address to latitude/longitude via OpenStreetMap Nominatim. "
        "Use before search_places or get_route when the user gives a place name. Free — no API key."
    )
    category = "web"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Place name or address to geocode (e.g. 'Times Square, NYC').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matches to return (1–10, default 5).",
                    },
                    "country_codes": {
                        "type": "string",
                        "description": "Optional ISO country codes, comma-separated (e.g. 'us,ca').",
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(
        self,
        query: str,
        limit: int = 5,
        country_codes: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            places = await get_osm_client().geocode(
                query, limit=limit, country_codes=country_codes
            )
        except OsmRateLimitError as e:
            return _err(e)
        except Exception as e:
            return _err(e)
        if not places:
            return {
                "success": False,
                "error": f"No geocode results for {query!r}. Try a more specific place name.",
            }
        top = places[0]
        return {
            "success": True,
            "result": f"{top['name']} → {top['lat']}, {top['lon']}",
            "places": places,
            "lat": top["lat"],
            "lon": top["lon"],
            "display_name": top.get("display_name"),
        }


@tool_display("Searching nearby places...")
class SearchPlacesTool(BaseTool):
    name = "search_places"
    description = (
        "Find points of interest near a lat/lon via OpenStreetMap (Overpass/Nominatim). "
        "Prefer amenity filters (restaurant, cafe, pharmacy, park, hotel, …). "
        "No Google ratings — OSM community data only. Call show_map afterward to display results."
    )
    category = "web"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Center latitude."},
                    "lon": {"type": "number", "description": "Center longitude."},
                    "amenity": {
                        "type": "string",
                        "description": (
                            "OSM amenity/tourism/shop type: restaurant, cafe, pharmacy, "
                            "hospital, park, hotel, supermarket, bank, atm, fuel, parking, …"
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Free-text search when amenity is not enough (e.g. 'pizza').",
                    },
                    "radius_m": {
                        "type": "integer",
                        "description": "Search radius in meters (default 1000, max 50000).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max places to return (default 15).",
                    },
                },
                "required": ["lat", "lon"],
            },
        }

    def validate_params(
        self,
        lat: float = None,
        lon: float = None,
        amenity: Optional[str] = None,
        query: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[bool, Optional[str]]:
        if lat is None or lon is None:
            return False, "lat and lon are required (geocode the area first)."
        if not amenity and not query:
            return False, "Provide amenity or query."
        return True, None

    async def execute(
        self,
        lat: float,
        lon: float,
        amenity: Optional[str] = None,
        query: Optional[str] = None,
        radius_m: int = 1000,
        limit: int = 15,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            places = await get_osm_client().search_places(
                lat=float(lat),
                lon=float(lon),
                amenity=amenity,
                query=query,
                radius_m=radius_m,
                limit=limit,
            )
        except (OsmRateLimitError, ValueError) as e:
            return _err(e)
        except Exception as e:
            return _err(e)

        label = amenity or query or "places"
        if not places:
            return {
                "success": True,
                "result": f"No {label} found within {radius_m}m.",
                "places": [],
                "count": 0,
                "center": {"lat": float(lat), "lon": float(lon)},
            }
        summary = "; ".join(
            f"{p['name']}" + (f" ({p['category']})" if p.get("category") else "")
            for p in places[:8]
        )
        return {
            "success": True,
            "result": f"Found {len(places)} {label}: {summary}",
            "places": places,
            "count": len(places),
            "center": {"lat": float(lat), "lon": float(lon)},
            "note": "OpenStreetMap data — no star ratings or Google reviews.",
        }


@tool_display("Calculating route...")
class GetRouteTool(BaseTool):
    name = "get_route"
    description = (
        "Turn-by-turn route between two coordinates via OSRM (driving, walking, or cycling). "
        "Geocode both ends first. Free public OSRM — best-effort, not live traffic."
    )
    category = "web"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_lat": {"type": "number"},
                    "origin_lon": {"type": "number"},
                    "dest_lat": {"type": "number"},
                    "dest_lon": {"type": "number"},
                    "mode": {
                        "type": "string",
                        "description": "driving (default), walking, or cycling.",
                    },
                },
                "required": ["origin_lat", "origin_lon", "dest_lat", "dest_lon"],
            },
        }

    async def execute(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        mode: str = "driving",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            route = await get_osm_client().get_route(
                origin_lat=float(origin_lat),
                origin_lon=float(origin_lon),
                dest_lat=float(dest_lat),
                dest_lon=float(dest_lon),
                mode=mode,
            )
        except (OsmRoutingError, OsmRateLimitError) as e:
            return _err(e)
        except Exception as e:
            return _err(e)
        return {
            "success": True,
            "result": (
                f"{route['distance_km']} km, ~{route['duration_min']} min "
                f"({route['mode']}, {route['step_count']} steps)"
            ),
            "route": route,
            "origin": {"lat": float(origin_lat), "lon": float(origin_lon)},
            "destination": {"lat": float(dest_lat), "lon": float(dest_lon)},
        }


def _coerce_places(places: Any) -> List[Dict[str, Any]]:
    if not isinstance(places, list):
        return []
    out: List[Dict[str, Any]] = []
    for p in places:
        if not isinstance(p, dict):
            continue
        try:
            lat = float(p.get("lat"))
            lon = float(p.get("lon"))
        except (TypeError, ValueError):
            continue
        if lat == 0 and lon == 0:
            continue
        out.append({
            "name": str(p.get("name") or p.get("display_name") or "Place").strip(),
            "lat": lat,
            "lon": lon,
            "category": str(p.get("category") or ""),
            "address": str(p.get("address") or p.get("display_name") or ""),
            "osm_url": p.get("osm_url"),
        })
    return out[:50]


def map_display_payload(result: Any) -> Dict[str, Any]:
    """Extract UI payload fields from a show_map tool result for stream broadcast."""
    if not isinstance(result, dict):
        return {}
    view = result.get("map_view")
    if not isinstance(view, dict):
        return {}
    places = _coerce_places(view.get("places"))
    center = view.get("center") if isinstance(view.get("center"), dict) else {}
    try:
        clat = float(center.get("lat")) if center.get("lat") is not None else (
            places[0]["lat"] if places else None
        )
        clon = float(center.get("lon")) if center.get("lon") is not None else (
            places[0]["lon"] if places else None
        )
    except (TypeError, ValueError):
        return {}
    if clat is None or clon is None:
        return {}
    zoom = view.get("zoom", 14)
    try:
        zoom_i = int(zoom)
    except (TypeError, ValueError):
        zoom_i = 14
    return {
        "map_view": {
            "title": str(view.get("title") or "Map").strip() or "Map",
            "center": {"lat": clat, "lon": clon},
            "zoom": max(1, min(zoom_i, 19)),
            "places": places,
        }
    }


@tool_display("Showing map: {title}...")
class ShowMapTool(BaseTool):
    name = "show_map"
    description = (
        "Display an interactive OpenStreetMap card in chat with markers. "
        "Call after search_places or geocode using REAL coordinates from those tools — never invent lat/lon."
    )
    category = "display"
    is_side_effecting = False

    def format_action(self, params: Dict[str, Any]) -> str:
        title = truncate_label(str(params.get("title") or ""))
        if title:
            return f'Showing map: "{title}"'
        return "Showing map"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Card title (e.g. 'Cafes near downtown').",
                    },
                    "places": {
                        "type": "array",
                        "description": "Markers from geocode/search_places results.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "lat": {"type": "number"},
                                "lon": {"type": "number"},
                                "category": {"type": "string"},
                                "address": {"type": "string"},
                                "osm_url": {"type": "string"},
                            },
                            "required": ["name", "lat", "lon"],
                        },
                    },
                    "center_lat": {
                        "type": "number",
                        "description": "Optional map center lat (defaults to first place).",
                    },
                    "center_lon": {
                        "type": "number",
                        "description": "Optional map center lon (defaults to first place).",
                    },
                    "zoom": {
                        "type": "integer",
                        "description": "Leaflet zoom level (default 14).",
                    },
                },
                "required": ["title", "places"],
            },
        }

    def validate_params(
        self,
        title: str = None,
        places: Any = None,
        **kwargs: Any,
    ) -> tuple[bool, Optional[str]]:
        if not title or not str(title).strip():
            return False, "title is required."
        coerced = _coerce_places(places)
        if not coerced:
            return False, "places must include at least one marker with valid lat/lon."
        return True, None

    async def execute(
        self,
        title: str,
        places: List[Dict[str, Any]],
        center_lat: Optional[float] = None,
        center_lon: Optional[float] = None,
        zoom: int = 14,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        coerced = _coerce_places(places)
        if not coerced:
            return {"success": False, "error": "No valid places with lat/lon."}
        if center_lat is not None and center_lon is not None:
            center = {"lat": float(center_lat), "lon": float(center_lon)}
        else:
            center = {"lat": coerced[0]["lat"], "lon": coerced[0]["lon"]}
        clean_title = str(title).strip()
        map_view = {
            "title": clean_title,
            "center": center,
            "zoom": int(zoom) if zoom else 14,
            "places": coerced,
        }
        return {
            "success": True,
            "result": f"Showing map: {clean_title} ({len(coerced)} places)",
            "map_view": map_view,
            "caption": clean_title,
        }
