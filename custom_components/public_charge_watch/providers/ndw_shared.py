"""Parts shared by the two NDW providers: download helper, tariff store, price attachment."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    DOMAIN,
    TARIFF_LAZY_AGE,
    TARIFF_MAX_AGE,
    TARIFF_RETRY,
    TARIFF_URL,
)
from .ndw_tariffs import scan_tariffs

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=180)


async def download(
    session: aiohttp.ClientSession, url: str, etag: str | None
) -> tuple[bytes, str | None] | None:
    """Return (raw, etag), or None when unchanged (HTTP 304)."""
    headers = {"If-None-Match": etag} if etag else {}
    async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
        if resp.status == 304:
            return None
        resp.raise_for_status()
        return await resp.read(), resp.headers.get("ETag")


class NdwTariffs:
    """The NDW tariff file: downloaded at most once a day, kept across restarts.

    Shared by every NDW provider, so one download serves them all.
    """

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        self._hass = hass
        self._session = session
        self._tariff_store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}_tariffs")
        self._tariff_lock = asyncio.Lock()
        self._tariff_state: dict[str, Any] | None = None  # loaded lazily from the store
        self._tariff_retry_after = 0.0

    async def async_get_tariffs(
        self, ids: set[str], lazy: bool = False
    ) -> dict[str, dict[str, Any]]:
        """Simplified tariffs for ids. Downloads at most once a day; kept across restarts.

        lazy: ids not met before (a moving search keeps finding new ones) only trigger a
        download when the last one is older than an hour.
        """
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
            age = time.time() - state["fetched"]
            due = age > TARIFF_MAX_AGE
            if lazy and age <= TARIFF_LAZY_AGE:
                new_ids = set()
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
        result = await download(self._session, TARIFF_URL, state["etag"] if use_etag else None)
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


def attach_price(loc: dict[str, Any], tariffs: dict[str, dict[str, Any]]) -> None:
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
