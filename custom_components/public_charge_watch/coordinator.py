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

from .const import (
    CONF_CONNECTORS,
    CONF_COORDS,
    CONF_MIN_POWER,
    CONF_ONLY_AVAILABLE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED,
    DYNAMIC_RESULTS,
    CONF_SOURCE,
    DOMAIN,
    CONF_LOCATION_IDS,
    SUBENTRY_LOCATION,
)
from .dynamic_location import pick
from .providers.base import (
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)

_LOGGER = logging.getLogger(__name__)
_STALE_LIMIT = 3  # update intervals the last data is kept while the source rate-limits us


def location_coords(entry: ConfigEntry) -> dict[str, tuple[float, float]]:
    """Stored coordinates of the entry's locations (for sources that look up by area)."""
    return {
        location_id: (float(lat), float(lon))
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_LOCATION
        for location_id, (lat, lon) in subentry.data.get(CONF_COORDS, {}).items()
    }


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
        self, hass: HomeAssistant, entry: ConfigEntry, client: Provider
    ) -> None:
        self.source: str = entry.data[CONF_SOURCE]
        default_interval, _ = client.scan_interval
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
        self.failed: set[str] = set()  # locations whose last request was rate limited
        self.location_success: dict[str, datetime] = {}  # last fresh data per location
        self.dynamic: dict[str, DynamicCoordinator] = {}  # by subentry id

    @property
    def location_ids(self) -> set[str]:
        return {i for _, i in iter_locations(self.config_entry)}

    @property
    def can_be_rate_limited(self) -> bool:
        return self._client.rate_limited

    @property
    def live_occupancy(self) -> bool:
        """Only NDW knows whether a charge point is free or in use."""
        return self._client.live_occupancy

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
        failed: set[str] = set()
        try:
            # reuse a recent download unless it is older than ~80% of our own interval
            locations = await self._client.async_get_locations(
                ids,
                force=force,
                max_age=0.8 * self.update_interval.total_seconds(),
                coords=location_coords(self.config_entry),
                failed=failed,
            )
        except ProviderAuthError as err:
            raise ConfigEntryAuthFailed(f"{self._client.name} rejected the credentials") from err
        except ProviderRateLimitError:
            locations, failed = {}, set(ids)
        except (aiohttp.ClientError, TimeoutError, ProviderError) as err:
            raise UpdateFailed(f"Error fetching charger data: {err}") from err
        now = dt_util.utcnow()
        self.failed = failed
        for location_id in locations:
            self.location_success[location_id] = now
        # A rate limit is the source's doing: a location that could not be fetched keeps its
        # last data for a while, and only then becomes "unavailable".
        for location_id in failed:
            last = self.location_success.get(location_id)
            if (
                last is not None
                and self.data
                and location_id in self.data
                and now - last < _STALE_LIMIT * self.update_interval
            ):
                locations[location_id] = self.data[location_id]
        if not locations:
            if failed:
                raise UpdateFailed("Error fetching charger data: the source is rate limiting")
            raise UpdateFailed(f"No location found in {self.source} data")
        if len(failed) < len(ids):
            self.last_success = now
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
        client: Provider,
    ) -> None:
        default_interval, _ = client.scan_interval
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
        self._force = False
        self.last_success: datetime | None = None

    @property
    def tracked_entity(self) -> str:
        return self.subentry.data[CONF_TRACKED]

    async def async_force_refresh(self) -> None:
        """Search now, checking the NDW file again (the refresh button)."""
        self._force = True
        await self.async_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        data = self.subentry.data
        force, self._force = self._force, False
        position = position_of(self.hass.states.get(self.tracked_entity))
        self.position = position
        if position is None:  # unknown / unavailable: show no charger rather than a stale one
            return {"position": None, "results": []}
        try:
            locations = await self._client.async_nearby_live(
                *position,
                data[CONF_RADIUS],
                max_age=0 if force else 0.8 * self.update_interval.total_seconds(),
            )
        except ProviderRateLimitError as err:
            if (
                self.data is not None
                and self.last_success is not None
                and dt_util.utcnow() - self.last_success < _STALE_LIMIT * self.update_interval
            ):
                _LOGGER.debug("%s; keeping the previous data", err)
                return self.data
            raise UpdateFailed(f"Error fetching charger data: {err}") from err
        except (aiohttp.ClientError, TimeoutError, ProviderError) as err:
            raise UpdateFailed(f"Error fetching charger data: {err}") from err
        self.last_success = dt_util.utcnow()
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
