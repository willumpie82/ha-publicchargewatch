"""Downloads of the NDW feeds: locations (shared, ETag) and tariffs (daily, persisted)."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
import logging
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    FEED_URL,
    MIN_FEED_AGE,
    TARIFF_MAX_AGE,
    TARIFF_RETRY,
    TARIFF_URL,
)
from .ndw_tariff import scan_tariffs
from .parser import find_by_ids, find_nearby

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=180)


class NdwFeed:
    """Shared client: one download serves every NDW config entry."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        self._hass = hass
        self._session = session
        self._lock = asyncio.Lock()
        self._etag: str | None = None
        self._pending_etag: str | None = None
        self._fetched = 0.0
        self._wanted: set[str] = set()
        self._cache: dict[str, dict[str, Any]] = {}

        self._tariff_store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}_tariffs"
        )
        self._tariff_lock = asyncio.Lock()
        self._tariff_state: dict[str, Any] | None = None  # loaded lazily from the store
        self._tariff_retry_after = 0.0

    async def _download(self, url: str, etag: str | None) -> tuple[bytes, str | None] | None:
        """Return (raw, etag), or None when unchanged (HTTP 304)."""
        headers = {"If-None-Match": etag} if etag else {}
        async with self._session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status == 304:
                return None
            resp.raise_for_status()
            return await resp.read(), resp.headers.get("ETag")

    # --- locations -------------------------------------------------------------

    async def async_download_raw(self) -> bytes:
        """The raw gzipped locations feed (used by the config flow)."""
        async with self._lock:
            result = await self._download(FEED_URL, None)
        assert result is not None
        return result[0]

    async def async_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        raw: bytes | None = None,
    ) -> list[dict[str, Any]]:
        """Locations around a point; pass the raw feed to avoid a second download."""
        if raw is None:
            raw = await self.async_download_raw()
        return await self._hass.async_add_executor_job(
            find_nearby, raw, latitude, longitude, radius_m
        )

    async def async_get_locations(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        """Current availability for the given location ids, with prices attached."""
        async with self._lock:
            self._wanted |= ids
            missing = ids - self._cache.keys()
            if missing or time.monotonic() - self._fetched > MIN_FEED_AGE:
                result = await self._download(FEED_URL, None if missing else self._etag)
                if result is not None:
                    raw, self._pending_etag = result
                    self._cache = await self._hass.async_add_executor_job(
                        find_by_ids, raw, set(self._wanted)
                    )
                    self._etag = self._pending_etag
                self._fetched = time.monotonic()
            # copies: prices are attached below and must not leak into the shared cache
            locations = {i: deepcopy(self._cache[i]) for i in ids if i in self._cache}

        tariff_ids = {
            t for loc in locations.values() for g in loc["groups"] for t in g["tariff_ids"]
        }
        tariffs = await self.async_get_tariffs(tariff_ids)
        for loc in locations.values():
            _attach_price(loc, tariffs)
        return locations

    # --- tariffs ---------------------------------------------------------------

    async def async_get_tariffs(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        """Simplified tariffs for ids. Downloads at most once a day; kept across restarts."""
        if not ids:
            return {}
        async with self._tariff_lock:
            if self._tariff_state is None:
                stored = await self._tariff_store.async_load() or {}
                self._tariff_state = {
                    "tariffs": stored.get("tariffs", {}),
                    "requested": set(stored.get("requested", [])),
                    "fetched": stored.get("fetched", 0.0),
                    "etag": stored.get("etag"),
                }
            state = self._tariff_state
            new_ids = ids - state["requested"]
            due = time.time() - state["fetched"] > TARIFF_MAX_AGE
            if (new_ids or due) and time.monotonic() > self._tariff_retry_after:
                try:
                    await self._refresh_tariffs(state, ids, use_etag=not new_ids)
                except (aiohttp.ClientError, TimeoutError) as err:
                    # keep the last known tariffs; availability must not depend on this
                    _LOGGER.warning("Could not refresh NDW tariffs: %s", err)
                    self._tariff_retry_after = time.monotonic() + TARIFF_RETRY
            return {i: state["tariffs"][i] for i in ids if i in state["tariffs"]}

    async def _refresh_tariffs(
        self, state: dict[str, Any], ids: set[str], use_etag: bool
    ) -> None:
        wanted = state["requested"] | ids
        result = await self._download(TARIFF_URL, state["etag"] if use_etag else None)
        if result is not None:
            raw, state["etag"] = result
            today: date = dt_util.now().date()
            state["tariffs"] = await self._hass.async_add_executor_job(
                scan_tariffs, raw, set(wanted), today
            )
        state["requested"] = wanted
        state["fetched"] = time.time()
        await self._tariff_store.async_save(
            {
                "tariffs": state["tariffs"],
                "requested": sorted(wanted),
                "fetched": state["fetched"],
                "etag": state["etag"],
            }
        )


def _attach_price(loc: dict[str, Any], tariffs: dict[str, dict[str, Any]]) -> None:
    """Add the headline price (first group with an energy price) and per-group prices."""
    loc["price_per_kwh"] = None
    loc["price_basis"] = "unknown"
    loc["price_text"] = None
    loc["price_details"] = {}
    prices: dict[str, float | None] = {}
    for group in loc["groups"]:
        tariff = next(
            (tariffs[t] for t in group["tariff_ids"] if t in tariffs), None
        )
        group["tariff"] = tariff
        prices[group["key"]] = tariff["energy_price"] if tariff else None
        if tariff and loc["price_per_kwh"] is None and tariff["energy_price"] is not None:
            loc["price_per_kwh"] = tariff["energy_price"]
            loc["price_basis"] = "tariff"
            loc["price_text"] = tariff["text"]
            loc["price_details"] = {
                k: tariff[k]
                for k in (
                    "start_fee",
                    "time_price_per_hour",
                    "parking_price_per_hour",
                    "vat",
                    "complex",
                    "last_updated",
                )
            }
    loc["prices_by_group"] = prices
