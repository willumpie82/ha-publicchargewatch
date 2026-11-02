"""Data update coordinator."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NdwFeed
from .const import (
    CONF_LOCATION_ID,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE,
    DOMAIN,
    SCAN_INTERVALS,
    SOURCE_NDW,
)
from .ocm import OcmAuthError, OcmClient

_LOGGER = logging.getLogger(__name__)


class NdwCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches one charging location from NDW or OCM."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: NdwFeed | OcmClient
    ) -> None:
        self.source: str = entry.data.get(CONF_SOURCE, SOURCE_NDW)
        default_interval, _ = SCAN_INTERVALS[self.source]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_LOCATION_ID]}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, default_interval)
            ),
        )
        self._client = client
        self.location_id: str = entry.data[CONF_LOCATION_ID]

    @property
    def live_occupancy(self) -> bool:
        """Only NDW knows whether a charge point is free or in use."""
        return self.source == SOURCE_NDW

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            locations = await self._client.async_get_locations({self.location_id})
        except OcmAuthError as err:
            raise ConfigEntryAuthFailed("Open Charge Map rejected the API key") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching charger data: {err}") from err
        if self.location_id not in locations:
            raise UpdateFailed(f"Location {self.location_id} not in {self.source} data")
        return locations[self.location_id]
