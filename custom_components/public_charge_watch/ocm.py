"""Open Charge Map client (no Home Assistant imports).

OCM has no live occupancy: its status says whether a connection is believed to be
in working order (last community/operator edit), not whether it is free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .parser import distance_m
from .provider import Provider, ProviderAuthError
from .tariff import parse_usage_cost

if TYPE_CHECKING:
    import aiohttp

OCM_URL = "https://api.openchargemap.io/v3/poi/"
ID_PREFIX = "ocm-"

# OCM StatusType IDs -> the (uppercase) status vocabulary used by the NDW source
STATUS_BY_ID = {
    0: "UNKNOWN",
    10: "AVAILABLE",
    20: "CHARGING",
    30: "INOPERATIVE",
    50: "OPERATIONAL",
    75: "OPERATIONAL",
    100: "OUTOFORDER",
    150: "PLANNED",
    200: "REMOVED",
    210: "REMOVED",
}


class OcmAuthError(ProviderAuthError):
    """The API key was rejected."""


def _status(*sources: tuple[dict[str, Any] | None, int | None]) -> str:
    """First usable status from (StatusType, StatusTypeID) pairs, connection first."""
    for obj, type_id in sources:
        type_id = type_id if type_id is not None else (obj or {}).get("ID")
        if type_id in STATUS_BY_ID:
            return STATUS_BY_ID[type_id]
        if obj and obj.get("IsOperational") is not None:
            return "OPERATIONAL" if obj["IsOperational"] else "OUTOFORDER"
    return "UNKNOWN"


def parse_poi(poi: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an OCM point of interest to the integration's location format."""
    address = poi.get("AddressInfo") or {}
    try:
        lat, lon = float(address["Latitude"]), float(address["Longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    poi_id = poi["ID"]
    poi_status = (poi.get("StatusType"), poi.get("StatusTypeID"))
    evses = []
    for index, conn in enumerate(poi.get("Connections") or []):
        power_kw = conn.get("PowerKW")
        conn_type = conn.get("ConnectionType") or {}
        evses.append(
            {
                "evse_id": f"{ID_PREFIX}{poi_id}-{conn.get('ID', index)}",
                "status": _status(
                    (conn.get("StatusType"), conn.get("StatusTypeID")), poi_status
                ),
                "last_updated": poi.get("DateLastStatusUpdate"),
                "connectors": [
                    {
                        "standard": conn_type.get("FormalName")
                        or conn_type.get("Title"),
                        "format": None,
                        "power_type": (conn.get("CurrentType") or {}).get("Title"),
                        "max_power_w": int(power_kw * 1000) if power_kw else None,
                        "quantity": conn.get("Quantity") or 1,  # identical sockets
                    }
                ],
            }
        )
    usage_cost = poi.get("UsageCost")
    price = parse_usage_cost(usage_cost)
    return {
        "price_text": usage_cost,
        "price_per_kwh": price["price_per_kwh"],
        "price_basis": price["basis"],
        "id": f"{ID_PREFIX}{poi_id}",
        "address": address.get("AddressLine1") or address.get("Title"),
        "city": address.get("Town"),
        "postal_code": address.get("Postcode"),
        "operator": (poi.get("OperatorInfo") or {}).get("Title"),
        "latitude": lat,
        "longitude": lon,
        "last_updated": poi.get("DateLastStatusUpdate"),
        "points": poi.get("NumberOfPoints"),
        "evses": evses,
    }


class OcmClient(Provider):
    """Thin async client for the OCM poi endpoint."""

    provider_id = "ocm"
    name = "Open Charge Map"
    live_occupancy = False
    needs_api_key = True
    scan_interval = (1800, 300)

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key

    async def _get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        import aiohttp

        params = {"output": "json", "compact": "false", "verbose": "false", **params}
        async with self._session.get(
            OCM_URL,
            params=params,
            headers={"X-API-Key": self._key},  # keeps the key out of URLs and logs
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status in (401, 403):
                raise OcmAuthError
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def async_validate(self) -> None:
        """Raise OcmAuthError if the key is rejected."""
        await self._get({"maxresults": 1, "countrycode": "NL"})

    async def async_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,  # noqa: ARG002 - every OCM call is fresh
    ) -> list[dict[str, Any]]:
        pois = await self._get(
            {
                "latitude": latitude,
                "longitude": longitude,
                "distance": max(radius_m, 1) / 1000,
                "distanceunit": "KM",
                "maxresults": 50,
            }
        )
        found = []
        for poi in pois:
            if (loc := parse_poi(poi)) is not None:
                loc["distance"] = round(
                    distance_m(latitude, longitude, loc["latitude"], loc["longitude"])
                )
                found.append(loc)
        return sorted(found, key=lambda loc: loc["distance"])

    async def async_get_locations(
        self,
        ids: set[str],
        force: bool = False,  # noqa: ARG002 - every OCM call is fresh
        max_age: float | None = None,  # noqa: ARG002
        coords: Any = None,  # noqa: ARG002
    ) -> dict[str, dict[str, Any]]:
        numeric = [i.removeprefix(ID_PREFIX) for i in ids]
        pois = await self._get(
            {"chargepointid": ",".join(numeric), "maxresults": len(numeric)}
        )
        locations = (parse_poi(poi) for poi in pois)
        return {loc["id"]: loc for loc in locations if loc is not None}
