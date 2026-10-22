"""
chad-maps — OpenStreetMap MCP server for CHAD / Claude Desktop.

Geocode, nearby places, routing, and map payload — no API keys.
Uses Nominatim, Overpass, and public OSRM (fair-use rate limits apply).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from src.infrastructure.maps.osm_client import (
    OsmRateLimitError,
    OsmRoutingError,
    get_osm_client,
)
from src.shared.logger import setup_logger

logger = setup_logger("chad-maps-server")
mcp = FastMCP("chad-maps")


@mcp.tool(meta={"hitl_class": "READ"})
async def geocode(
    query: str,
    limit: int = 5,
    country_codes: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a place name or address to latitude/longitude (OpenStreetMap Nominatim)."""
    try:
        places = await get_osm_client().geocode(
            query, limit=limit, country_codes=country_codes
        )
    except OsmRateLimitError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("geocode failed")
        return {"success": False, "error": str(e)}
    if not places:
        return {"success": False, "error": f"No results for {query!r}"}
    top = places[0]
    return {
        "success": True,
        "result": f"{top['name']} → {top['lat']}, {top['lon']}",
        "places": places,
        "lat": top["lat"],
        "lon": top["lon"],
    }


@mcp.tool(meta={"hitl_class": "READ"})
async def search_places(
    lat: float,
    lon: float,
    amenity: Optional[str] = None,
    query: Optional[str] = None,
    radius_m: int = 1000,
    limit: int = 15,
) -> Dict[str, Any]:
    """Find POIs near lat/lon. Prefer amenity (restaurant, cafe, pharmacy, …)."""
    try:
        places = await get_osm_client().search_places(
            lat=lat,
            lon=lon,
            amenity=amenity,
            query=query,
            radius_m=radius_m,
            limit=limit,
        )
    except (OsmRateLimitError, ValueError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("search_places failed")
        return {"success": False, "error": str(e)}
    return {
        "success": True,
        "result": f"Found {len(places)} places",
        "places": places,
        "count": len(places),
        "center": {"lat": lat, "lon": lon},
        "note": "OpenStreetMap data — no star ratings.",
    }


@mcp.tool(meta={"hitl_class": "READ"})
async def get_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    mode: str = "driving",
) -> Dict[str, Any]:
    """Turn-by-turn route via OSRM (driving, walking, cycling)."""
    try:
        route = await get_osm_client().get_route(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            mode=mode,
        )
    except (OsmRoutingError, OsmRateLimitError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("get_route failed")
        return {"success": False, "error": str(e)}
    return {
        "success": True,
        "result": f"{route['distance_km']} km, ~{route['duration_min']} min",
        "route": route,
    }


@mcp.tool(meta={"hitl_class": "READ"})
async def show_map(
    title: str,
    places: List[Dict[str, Any]],
    center_lat: Optional[float] = None,
    center_lon: Optional[float] = None,
    zoom: int = 14,
) -> Dict[str, Any]:
    """
    Build a map_view payload for hosts that render interactive maps.
    Pass real coordinates from geocode/search_places — never invent lat/lon.
    """
    if not title or not str(title).strip():
        return {"success": False, "error": "title is required"}
    if not places:
        return {"success": False, "error": "places required"}
    # Reuse native tool validation via payload helper path
    from src.tools.maps.osm_tools import ShowMapTool

    tool = ShowMapTool()
    return await tool.execute(
        title=title,
        places=places,
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
    )


if __name__ == "__main__":
    mcp.run()
