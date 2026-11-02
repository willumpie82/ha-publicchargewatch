"""Streaming parser for the NDW GeoJSON locations feed (no Home Assistant imports).

The GeoJSON variant is ~5 MB (41 MB unpacked) against 18 MB (200 MB) for the OCPI one. It has
counts per connector group (total/available) instead of a status per charge point.
"""

from __future__ import annotations

from collections.abc import Callable
import gzip
import io
import math
from typing import Any

import ijson

EARTH_RADIUS_M = 6_371_000

CONNECTOR_NAMES = {
    "IEC_62196_T1": "Type 1",
    "IEC_62196_T1_COMBO": "CCS1",
    "IEC_62196_T2": "Type 2",
    "IEC_62196_T2_COMBO": "CCS",
    "CHADEMO": "CHAdeMO",
    "DOMESTIC_F": "Schuko",
    "TESLA_S": "Tesla",
}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def group_label(group: dict[str, Any]) -> str:
    """'Type 2 · 11 kW' style label for a connector group."""
    name = CONNECTOR_NAMES.get(group.get("connector_type") or "", group.get("connector_type"))
    parts = [name or "?"]
    if power := group.get("power_w"):
        parts.append(f"{round(power / 1000, 1):g} kW")
    return " · ".join(parts)


def _groups(availabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Groups with a stable key (the feed has no ids, so sort and number duplicates)."""
    raw = sorted(
        availabilities,
        key=lambda a: (
            a.get("connector_type") or "",
            a.get("power_max") or 0,
            a.get("power_type") or "",
            sorted(a.get("tariff_ids") or []),
        ),
    )
    seen: dict[str, int] = {}
    groups = []
    for a in raw:
        base = f"{a.get('connector_type')}_{int(a.get('power_max') or 0)}_{a.get('power_type')}"
        seen[base] = seen.get(base, 0) + 1
        groups.append(
            {
                "key": base if seen[base] == 1 else f"{base}_{seen[base]}",
                "connector_type": a.get("connector_type"),
                "connector_format": a.get("connector_format"),
                "power_type": a.get("power_type"),
                "power_w": int(a["power_max"]) if a.get("power_max") else None,
                "total": a.get("total") or 0,
                "available": a.get("available") or 0,
                "tariff_ids": a.get("tariff_ids") or [],
            }
        )
    return groups


def slim_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a GeoJSON feature to the fields this integration uses."""
    props = feature.get("properties") or {}
    try:
        lon, lat = feature["geometry"]["coordinates"][:2]
        lat, lon = float(lat), float(lon)
    except (KeyError, TypeError, ValueError):
        return None
    groups = _groups(props.get("availabilities") or [])
    return {
        "id": feature["id"],
        "address": props.get("address"),
        "city": None,  # not in this feed
        "postal_code": None,
        "operator": props.get("operator_name") or props.get("owner_name"),
        "latitude": lat,
        "longitude": lon,
        "last_updated": props.get("last_updated"),
        "points": sum(g["total"] for g in groups),
        "groups": groups,
    }


def scan(
    raw: bytes,
    keep_coords: Callable[[float, float], bool] | None = None,
    keep_id: Callable[[str], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Stream the gzipped feed and return slimmed locations passing the filters.

    Filters run on the raw feature first, so the ~81k non-matching features are cheap.
    """
    found: dict[str, dict[str, Any]] = {}
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        for feature in ijson.items(stream, "features.item", use_float=True):
            if keep_id is not None and not keep_id(feature.get("id")):
                continue
            if keep_coords is not None:
                try:
                    lon, lat = feature["geometry"]["coordinates"][:2]
                except (KeyError, TypeError, ValueError):
                    continue
                if not keep_coords(float(lat), float(lon)):
                    continue
            if (slim := slim_feature(feature)) is not None:
                found[slim["id"]] = slim
    return found


def find_nearby(
    raw: bytes, latitude: float, longitude: float, radius_m: float
) -> list[dict[str, Any]]:
    """Locations within radius_m, nearest first, each with a 'distance' field."""
    found = scan(
        raw,
        keep_coords=lambda lat, lon: distance_m(latitude, longitude, lat, lon) <= radius_m,
    )
    for loc in found.values():
        loc["distance"] = round(
            distance_m(latitude, longitude, loc["latitude"], loc["longitude"])
        )
    return sorted(found.values(), key=lambda loc: loc["distance"])


def find_by_ids(raw: bytes, ids: set[str]) -> dict[str, dict[str, Any]]:
    """Locations with one of the given ids."""
    return scan(raw, keep_id=lambda i: i in ids)
