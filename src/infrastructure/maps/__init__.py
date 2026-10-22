"""OpenStreetMap-backed geocoding, POI search, and routing (no API key)."""

from src.infrastructure.maps.osm_client import OsmClient, get_osm_client

__all__ = ["OsmClient", "get_osm_client"]
