"""Pick the best chargers around a position (no Home Assistant imports)."""

from __future__ import annotations

from typing import Any

from .parser import CONNECTOR_NAMES


def navigate_url(latitude: float, longitude: float) -> str:
    """Link that starts turn-by-turn navigation (opens the maps app on a phone)."""
    return f"https://www.google.com/maps/dir/?api=1&destination={latitude:.6f},{longitude:.6f}"


def _price(loc: dict[str, Any], groups: list[dict[str, Any]]) -> float | None:
    """Cheapest known energy price among the matching groups, else the headline price."""
    prices = [
        p for g in groups if (p := loc.get("prices_by_group", {}).get(g["key"])) is not None
    ]
    return min(prices) if prices else loc.get("price_per_kwh")


def pick(
    locations: list[dict[str, Any]],
    connectors: list[str],
    min_power_kw: float,
    only_available: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Best matches, free ones first, then nearest first.

    A location qualifies when one of its connector groups has a wanted connector type and
    enough power; free/total/power/price then describe only those groups.
    """
    results = []
    for loc in locations:
        groups = [
            g
            for g in loc["groups"]
            if (not connectors or g["connector_type"] in connectors)
            and (g["power_w"] or 0) >= min_power_kw * 1000
        ]
        if not groups:
            continue
        free = sum(g["available"] for g in groups)
        if only_available and not free:
            continue
        results.append(
            {
                "id": loc["id"],
                "address": loc["address"],
                "operator": loc.get("operator"),
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "distance_m": loc["distance"],
                "free": free,
                "total": sum(g["total"] for g in groups),
                "power_kw": round(max((g["power_w"] or 0) for g in groups) / 1000, 1),
                "connectors": sorted(
                    {CONNECTOR_NAMES.get(g["connector_type"], g["connector_type"]) for g in groups}
                ),
                "price_per_kwh": _price(loc, groups),
                "navigate_url": navigate_url(loc["latitude"], loc["longitude"]),
            }
        )
    results.sort(key=lambda r: (r["free"] == 0, r["distance_m"]))
    return results[:limit]
