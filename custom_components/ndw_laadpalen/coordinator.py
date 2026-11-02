"""Data update coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NdwFeed
from .const import (
    CONF_LOCATION_ID,
    SUBENTRY_LOCATION,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE,
    DOMAIN,
    SCAN_INTERVALS,
    SOURCE_NDW,
)
from .ocm import OcmAuthError, OcmClient

_LOGGER = logging.getLogger(__name__)


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

    @property
    def location_ids(self) -> set[str]:
        return {
            sub.data[CONF_LOCATION_ID]
            for sub in self.config_entry.subentries.values()
            if sub.subentry_type == SUBENTRY_LOCATION
        }

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
