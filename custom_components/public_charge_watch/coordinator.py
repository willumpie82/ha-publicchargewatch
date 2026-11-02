"""Data update coordinator."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NdwFeed
from .const import (
    CONF_CONNECTORS,
    CONF_MIN_POWER,
    CONF_ONLY_AVAILABLE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED,
    DYNAMIC_RESULTS,
    CONF_SOURCE,
    DOMAIN,
    CONF_LOCATION_IDS,
    SCAN_INTERVALS,
    SUBENTRY_LOCATION,
    SOURCE_NDW,
)
from .dynamic import pick
from .ocm import OcmAuthError, OcmClient

_LOGGER = logging.getLogger(__name__)


def iter_locations(entry: ConfigEntry) -> Iterator[tuple[str, str]]:
    """(subentry_id, location_id) for every charging location of the entry."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_LOCATION:
            for location_id in subentry.data[CONF_LOCATION_IDS]:
                yield subentry.subentry_id, location_id



class NdwCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetches every charging location of one source (an entry), keyed by location id."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: NdwFeed | OcmClient
    ) -> None:
        self.source: str = entry.data[CONF_SOURCE]
        default_interval, _ = SCAN_INTERVALS[self.source]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.source}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, default_interval)
            ),
        )
        self._client = client
        self._force = False
        self.last_success: datetime | None = None
        self.dynamic: dict[str, DynamicCoordinator] = {}  # by subentry id

    @property
    def location_ids(self) -> set[str]:
        return {i for _, i in iter_locations(self.config_entry)}

    @property
    def live_occupancy(self) -> bool:
        """Only NDW knows whether a charge point is free or in use."""
        return self.source == SOURCE_NDW

    async def async_force_refresh(self) -> None:
        """Refresh now, bypassing the shared feed's minimum age (the refresh button)."""
        self._force = True
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        ids = self.location_ids
        if not ids:
            self.last_success = dt_util.utcnow()
            return {}
        force, self._force = self._force, False
        try:
            # reuse a recent download unless it is older than ~80% of our own interval
            locations = await self._client.async_get_locations(
                ids,
                force=force,
                max_age=0.8 * self.update_interval.total_seconds(),
            )
        except OcmAuthError as err:
            raise ConfigEntryAuthFailed("Open Charge Map rejected the API key") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching charger data: {err}") from err
        if not locations:
            raise UpdateFailed(f"No location found in {self.source} data")
        self.last_success = dt_util.utcnow()
        return locations  # a missing location only makes its own entities unavailable


def position_of(state: State | None) -> tuple[float, float] | None:
    """(latitude, longitude) of a person / device tracker / zone state, if it has one."""
    if state is None:
        return None
    try:
        return float(state.attributes["latitude"]), float(state.attributes["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


class DynamicCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """The best chargers around a tracked entity: {"position": (lat, lon) | None, "results": [...]}.

    Runs on the entry's interval, and again when the tracked entity has moved (see
    __init__.py). It searches the NDW feed the entry already holds, so moving costs no download.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        client: NdwFeed,
    ) -> None:
        default_interval, _ = SCAN_INTERVALS[SOURCE_NDW]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, default_interval)
            ),
        )
        self.subentry = subentry
        self._client = client
        self.position: tuple[float, float] | None = None  # where the data was computed

    @property
    def tracked_entity(self) -> str:
        return self.subentry.data[CONF_TRACKED]

    async def _async_update_data(self) -> dict[str, Any]:
        data = self.subentry.data
        position = position_of(self.hass.states.get(self.tracked_entity))
        self.position = position
        if position is None:  # unknown / unavailable: show no charger rather than a stale one
            return {"position": None, "results": []}
        try:
            locations = await self._client.async_nearby_live(
                *position,
                data[CONF_RADIUS],
                max_age=0.8 * self.update_interval.total_seconds(),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching charger data: {err}") from err
        return {
            "position": position,
            "results": pick(
                locations,
                list(data.get(CONF_CONNECTORS, [])),
                float(data.get(CONF_MIN_POWER, 0)),
                bool(data.get(CONF_ONLY_AVAILABLE, True)),
                DYNAMIC_RESULTS,
            ),
        }
