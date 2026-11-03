"""NDW DOT-NL REST API provider: asks only for the area it needs (no Home Assistant imports).

The same data as the bulk download (`api.NdwFeed`), but NDW filters by bounding box, so a refresh is
a few kB instead of ~5 MB. The API has no lookup by id, so known locations are fetched by their
coordinates, which the places therefore store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

from ..const import (
    DOTNL_LOCATION_RADIUS_M,
    DOTNL_MAX_BBOX_DEG2,
    DOTNL_REQUEST_GAP,
    DOTNL_URL,
)
from ..geo import distance_m
from .base import Provider, ProviderError, ProviderRateLimitError
from .ndw_geojson import slim_feature
from .ndw_shared import NdwTariffs, attach_price

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_METERS_PER_DEGREE = 111_320
_MAX_PAGES = 20  # of 1000 locations: far more than any search area holds
_RETRY_DELAY = 2.0  # seconds to wait before the one retry after a 429 without Retry-After
_MAX_RETRY_WAIT = 10.0
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def bounding_box(latitude: float, longitude: float, radius_m: float) -> str:
    """'min_lon,min_lat,max_lon,max_lat' around a point; the API caps the area."""
    d_lat = radius_m / _METERS_PER_DEGREE
    d_lon = radius_m / (_METERS_PER_DEGREE * max(math.cos(math.radians(latitude)), 1e-6))
    if (2 * d_lat) * (2 * d_lon) > DOTNL_MAX_BBOX_DEG2:
        raise ProviderError("The search area is larger than the NDW API allows")
    return ",".join(
        f"{v:.6f}"
        for v in (longitude - d_lon, latitude - d_lat, longitude + d_lon, latitude + d_lat)
    )


def _retry_after(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


class NdwApi(Provider):
    """NDW through its bounding-box API."""

    provider_id = "ndw_api"
    name = "NDW (live API)"
    live_occupancy = True
    city_from_geocoder = True  # no town in the NDW data
    rate_limited = True
    scan_interval = (300, 60)

    def __init__(self, session: aiohttp.ClientSession, tariffs: NdwTariffs) -> None:
        self._session = session
        self._tariffs = tariffs
        self._gap = asyncio.Lock()  # one request at a time, spaced out
        self._last_request = 0.0
        self.lookup_radius_m = DOTNL_LOCATION_RADIUS_M  # set from the entry's options

    async def _fetch(self, bbox: str) -> list[dict[str, Any]]:
        """All locations in the area, slimmed (large areas come in pages of 1000)."""
        features: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0
        for _ in range(_MAX_PAGES):
            page, cursor, total = await self._fetch_page(bbox, cursor)
            features += page
            pages += 1
            if cursor is None:
                break
        else:
            raise ProviderError("The NDW API returned too many pages")
        if total is not None and total > len(features):
            # The API's cursor paging drops a few locations at page edges (e.g. 1741 of 1748):
            # tolerated for multi-page answers, but a single page must be complete.
            if pages == 1 or total - len(features) > max(10, total // 100):
                raise ProviderError("The NDW API returned only part of the locations")
            _LOGGER.debug("NDW API returned %s of %s locations", len(features), total)
        return [slim for feature in features if (slim := slim_feature(feature)) is not None]

    async def _fetch_page(
        self, bbox: str, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        """One page; a rate-limited request is tried once more after a short wait."""
        try:
            return await self._request_page(bbox, cursor)
        except ProviderRateLimitError as err:
            await asyncio.sleep(min(err.retry_after or _RETRY_DELAY, _MAX_RETRY_WAIT))
            return await self._request_page(bbox, cursor)

    async def _request_page(
        self, bbox: str, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        """One request: (features, cursor of the next page, total count)."""
        params = {"bbox": bbox}
        if cursor:
            params["cursor"] = cursor
        async with self._gap:
            wait = self._last_request + DOTNL_REQUEST_GAP - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._session.get(
                    DOTNL_URL, params=params, timeout=_TIMEOUT
                ) as resp:
                    if resp.status == 429:
                        retry_after = _retry_after(resp.headers.get("Retry-After"))
                        hints = {
                            k: v
                            for k, v in resp.headers.items()
                            if "limit" in k.lower() or "retry" in k.lower() or "remaining" in k.lower()
                        }
                        text = (await resp.text())[:200].strip()  # may name the window
                        raise ProviderRateLimitError(
                            f"NDW API rate limit (Retry-After: {retry_after}, headers: {hints}, body: {text!r})",
                            retry_after,
                        )
                    resp.raise_for_status()
                    body = await resp.read()
                    total = resp.headers.get("x-total-count")
                    link = resp.headers.get("Link", "")
            finally:
                self._last_request = time.monotonic()
        try:
            features = (await asyncio.to_thread(json.loads, body))["features"]
        except (ValueError, KeyError, TypeError) as err:
            raise ProviderError("The NDW API returned an unexpected answer") from err
        # The Link header's own URL has a wrong path; only its cursor is usable.
        next_link = _NEXT_LINK.search(link)
        next_cursor = (
            parse_qs(urlsplit(next_link.group(1)).query).get("cursor", [None])[0]
            if next_link
            else None
        )
        return features, next_cursor, int(total) if total and total.isdigit() else None

    async def async_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,  # noqa: ARG002 - the API is always current
    ) -> list[dict[str, Any]]:
        found = []
        for loc in await self._fetch(bounding_box(latitude, longitude, radius_m)):
            loc["distance"] = round(
                distance_m(latitude, longitude, loc["latitude"], loc["longitude"])
            )
            if loc["distance"] <= radius_m:
                found.append(loc)
        return sorted(found, key=lambda loc: loc["distance"])

    async def async_nearby_live(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,
    ) -> list[dict[str, Any]]:
        locations = await self.async_nearby(latitude, longitude, radius_m, max_age)
        ids = {t for loc in locations for g in loc["groups"] for t in g["tariff_ids"]}
        tariffs = await self._tariffs.async_get_tariffs(ids, lazy=True)
        for loc in locations:
            attach_price(loc, tariffs)
        return locations

    async def async_get_locations(
        self,
        ids: set[str],
        force: bool = False,  # noqa: ARG002
        max_age: float | None = None,  # noqa: ARG002
        coords: Mapping[str, tuple[float, float]] | None = None,
        failed: set[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Known locations, found through small areas around their stored coordinates.

        Locations without coordinates cannot be looked up and are left out.
        """
        coords = coords or {}
        remaining = {i: tuple(coords[i]) for i in ids if i in coords}
        found: dict[str, dict[str, Any]] = {}
        while remaining:
            pivot_id, (lat, lon) = next(iter(remaining.items()))
            covered = {
                i
                for i, c in remaining.items()
                if i == pivot_id or distance_m(lat, lon, *c) <= self.lookup_radius_m
            }
            try:
                for loc in await self._fetch(bounding_box(lat, lon, self.lookup_radius_m)):
                    if loc["id"] in ids:
                        found[loc["id"]] = loc
            except ProviderRateLimitError:
                if failed is None:
                    raise
                failed.update(covered)  # the other areas may still work
            remaining = {i: c for i, c in remaining.items() if i not in covered}
        if failed is not None:
            failed.difference_update(found)
        tariff_ids = {
            t for loc in found.values() for g in loc["groups"] for t in g["tariff_ids"]
        }
        tariffs = await self._tariffs.async_get_tariffs(tariff_ids)
        for loc in found.values():
            attach_price(loc, tariffs)
        return found
