"""Unit tests for OSM client helpers and show_map display payload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.maps.osm_client import (
    OsmClient,
    _normalize_nominatim,
    _normalize_overpass,
    _resolve_amenity,
)
from src.tools.maps.osm_tools import (
    ShowMapTool,
    map_display_payload,
    _coerce_places,
)


def test_resolve_amenity_aliases():
    assert _resolve_amenity("cafe") == ("amenity", "cafe")
    assert _resolve_amenity("hotel") == ("tourism", "hotel")
    assert _resolve_amenity("supermarket") == ("shop", "supermarket")
    assert _resolve_amenity("Custom Spot") == ("amenity", "custom_spot")


def test_normalize_nominatim():
    item = {
        "lat": "40.758",
        "lon": "-73.9855",
        "display_name": "Times Square, New York, USA",
        "name": "Times Square",
        "osm_type": "way",
        "osm_id": 123,
        "type": "attraction",
        "class": "tourism",
        "address": {"city": "New York"},
    }
    out = _normalize_nominatim(item)
    assert out["lat"] == 40.758
    assert out["lon"] == -73.9855
    assert out["name"] == "Times Square"
    assert out["osm_url"] == "https://www.openstreetmap.org/way/123"


def test_normalize_overpass_center():
    el = {
        "type": "way",
        "id": 99,
        "center": {"lat": 48.86, "lon": 2.29},
        "tags": {"name": "Cafe Test", "amenity": "cafe", "addr:street": "Rue A"},
    }
    out = _normalize_overpass(el)
    assert out["name"] == "Cafe Test"
    assert out["category"] == "cafe"
    assert out["lat"] == 48.86
    assert "Rue A" in out["address"]


def test_coerce_places_skips_invalid():
    places = _coerce_places([
        {"name": "A", "lat": 1.0, "lon": 2.0},
        {"name": "bad"},
        {"name": "B", "lat": "3.5", "lon": "4.5", "category": "park"},
    ])
    assert len(places) == 2
    assert places[1]["lat"] == 3.5


def test_map_display_payload():
    result = {
        "success": True,
        "map_view": {
            "title": "Cafes",
            "center": {"lat": 1.0, "lon": 2.0},
            "zoom": 14,
            "places": [{"name": "X", "lat": 1.0, "lon": 2.0, "category": "cafe"}],
        },
    }
    payload = map_display_payload(result)
    assert payload["map_view"]["title"] == "Cafes"
    assert payload["map_view"]["places"][0]["name"] == "X"
    assert map_display_payload({"success": True}) == {}


@pytest.mark.asyncio
async def test_show_map_tool_execute():
    tool = ShowMapTool()
    out = await tool.execute(
        title="Test Map",
        places=[{"name": "Spot", "lat": 40.0, "lon": -74.0, "category": "park"}],
    )
    assert out["success"] is True
    assert out["map_view"]["title"] == "Test Map"
    assert len(out["map_view"]["places"]) == 1


@pytest.mark.asyncio
async def test_show_map_rejects_empty_places():
    tool = ShowMapTool()
    ok, err = tool.validate_params(title="T", places=[])
    assert ok is False
    assert err


@pytest.mark.asyncio
async def test_geocode_throttle_and_parse():
    client = OsmClient(nominatim_min_interval=0.01)
    fake_json = [
        {
            "lat": "1.1",
            "lon": "2.2",
            "display_name": "Place, City",
            "name": "Place",
            "osm_type": "node",
            "osm_id": 1,
            "type": "yes",
            "class": "place",
            "address": {},
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=fake_json)

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.infrastructure.maps.osm_client.httpx.AsyncClient", return_value=mock_client):
        results = await client.geocode("Place")
        assert len(results) == 1
        assert results[0]["lat"] == 1.1
        # Second call exercises throttle path without sleeping long
        results2 = await client.geocode("Place")
        assert results2[0]["name"] == "Place"
