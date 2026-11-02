"""Live status of public EV chargers, from the NDW open data feed or Open Charge Map."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NdwFeed
from .const import (
    CONF_API_KEY,
    CONF_LOCATION_IDS,
    CONF_SOURCE,
    DOMAIN,
    SOURCE_NDW,
    SOURCE_OCM,
)
from .coordinator import NdwCoordinator, iter_locations
from .ocm import OcmClient

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

type NdwConfigEntry = ConfigEntry[NdwCoordinator]


def get_client(hass: HomeAssistant, data: Mapping[str, Any]) -> NdwFeed | OcmClient:
    """Client for the source in data; NDW is shared by all entries."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)
    if data.get(CONF_SOURCE, SOURCE_NDW) == SOURCE_OCM:
        key = data[CONF_API_KEY]
        clients = domain_data.setdefault("ocm", {})
        if key not in clients:
            clients[key] = OcmClient(session, key)
        return clients[key]
    if "feed" not in domain_data:
        domain_data["feed"] = NdwFeed(hass, session)
    return domain_data["feed"]


async def async_setup_entry(hass: HomeAssistant, entry: NdwConfigEntry) -> bool:
    coordinator = NdwCoordinator(hass, entry, get_client(hass, entry.data))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NdwConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NdwConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Deleting a charging location's device removes it from its place."""
    ids = {i for domain, i in device_entry.identifiers if domain == DOMAIN}
    for subentry in entry.subentries.values():
        remaining = [i for i in subentry.data.get(CONF_LOCATION_IDS, []) if i not in ids]
        if len(remaining) == len(subentry.data.get(CONF_LOCATION_IDS, [])):
            continue
        if remaining:
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, CONF_LOCATION_IDS: remaining}
            )
        else:
            hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
        return True
    return False


async def _async_reload(hass: HomeAssistant, entry: NdwConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
