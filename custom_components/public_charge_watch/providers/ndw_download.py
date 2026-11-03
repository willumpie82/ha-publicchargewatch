"""Downloads of the NDW feeds: locations (shared, ETag) and tariffs (daily, persisted)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant

from ..const import FEED_URL, MIN_FEED_AGE
from .base import Provider
from .ndw_geojson import find_by_ids, find_nearby
from .ndw_shared import NdwTariffs, attach_price, download


class NdwFeed(Provider):
    """Shared client: one download serves every NDW config entry."""

    provider_id = "ndw"
    name = "NDW"
    live_occupancy = True
    bulk_download = True
    city_from_geocoder = True  # the NDW feed has no town
    scan_interval = (300, 120)

    def __init__(
        self, hass: HomeAssistant, session: aiohttp.ClientSession, tariffs: NdwTariffs
    ) -> None:
        self._hass = hass
        self._session = session
        self._lock = asyncio.Lock()
        self._raw: bytes | None = None  # the feed behind _cache (~5 MB)
        self._cache_stale = False
        self._etag: str | None = None
        self._fetched = 0.0
        self._wanted: set[str] = set()
        self._cache: dict[str, dict[str, Any]] = {}
        self._tariffs = tariffs

    # --- locations -------------------------------------------------------------

    async def _refresh_raw(self, force: bool, max_age: float | None) -> bool:
        """Make sure the raw feed is fresh enough (caller holds the lock).

        Returns True when new data was downloaded. The ETag belongs to the raw feed we
        hold; without a raw feed there is no ETag to send.
        """
        limit = MIN_FEED_AGE if max_age is None else max_age
        if (
            not force
            and self._raw is not None
            and time.monotonic() - self._fetched <= limit
        ):
            return False
        result = await download(
            self._session,
            FEED_URL,
            self._etag if self._raw is not None else None,
        )
        self._fetched = time.monotonic()
        if result is None:  # 304: unchanged
            return False
        self._raw, self._etag = result
        return True

    def is_fresh(self, max_age: float) -> bool:
        """Whether the held feed is young enough to be used without downloading."""
        return self._raw is not None and time.monotonic() - self._fetched <= max_age

    async def async_prepare(self, max_age: float | None = None) -> None:
        await self.async_raw(max_age=max_age)

    async def async_raw(
        self, force: bool = False, max_age: float | None = None
    ) -> bytes:
        """The raw gzipped feed (config flows); shared with the coordinator."""
        async with self._lock:
            if await self._refresh_raw(force, max_age):
                self._cache_stale = True
            assert self._raw is not None
            return self._raw

    async def async_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,
    ) -> list[dict[str, Any]]:
        """Locations around a point, from the shared feed."""
        raw = await self.async_raw(max_age=max_age)
        return await self._hass.async_add_executor_job(
            find_nearby, raw, latitude, longitude, radius_m
        )

    async def async_nearby_live(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,
    ) -> list[dict[str, Any]]:
        """Nearby locations with prices attached (known tariffs; new ones at most hourly)."""
        locations = await self.async_nearby(latitude, longitude, radius_m, max_age)
        tariff_ids = {
            t for loc in locations for g in loc["groups"] for t in g["tariff_ids"]
        }
        tariffs = await self._tariffs.async_get_tariffs(tariff_ids, lazy=True)
        for loc in locations:
            attach_price(loc, tariffs)
        return locations

    async def async_get_locations(
        self,
        ids: set[str],
        force: bool = False,
        max_age: float | None = None,
        coords: Mapping[str, tuple[float, float]] | None = None,  # noqa: ARG002
        failed: set[str] | None = None,  # noqa: ARG002
    ) -> dict[str, dict[str, Any]]:
        """Current availability for the given location ids, with prices attached.

        All NDW entries share one download: data younger than max_age is reused (the
        coordinator passes most of its update interval). force skips that (manual
        refresh); the ETag still avoids downloading an unchanged file. The raw feed is
        kept, so a newly added location is found without another download.
        """
        async with self._lock:
            self._wanted |= ids
            if await self._refresh_raw(force, max_age):
                self._cache_stale = True
            if self._cache_stale or ids - self._cache.keys():
                self._cache = await self._hass.async_add_executor_job(
                    find_by_ids, self._raw, set(self._wanted)
                )
                self._cache_stale = False
            # copies: prices are attached below and must not leak into the shared cache
            locations = {i: deepcopy(self._cache[i]) for i in ids if i in self._cache}

        tariff_ids = {
            t for loc in locations.values() for g in loc["groups"] for t in g["tariff_ids"]
        }
        tariffs = await self._tariffs.async_get_tariffs(tariff_ids)
        for loc in locations.values():
            attach_price(loc, tariffs)
        return locations
