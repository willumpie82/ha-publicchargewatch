"""Live status of public EV chargers, from the NDW open data feed or Open Charge Map."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

from .api import NdwFeed
from .const import (
    CONF_API_KEY,
    CONF_LOCATION_IDS,
    CONF_SOURCE,
    DOMAIN,
    DYNAMIC_MOVE_M,
    SUBENTRY_DYNAMIC,
    SOURCE_NDW,
    SOURCE_OCM,
)
from .coordinator import DynamicCoordinator, NdwCoordinator, position_of
from .ocm import OcmClient
from .parser import distance_m
from .provider import Provider

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
]

type NdwConfigEntry = ConfigEntry[NdwCoordinator]


def get_client(hass: HomeAssistant, data: Mapping[str, Any]) -> Provider:
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
    labels = [
        await _async_setup_dynamic(hass, entry, coordinator, subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_DYNAMIC
    ]
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for label in labels:  # the devices exist now
        label()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_setup_dynamic(
    hass: HomeAssistant, entry: NdwConfigEntry, coordinator: NdwCoordinator, subentry
) -> Callable[[], None]:
    dynamic = DynamicCoordinator(hass, entry, subentry, get_client(hass, entry.data))
    await dynamic.async_config_entry_first_refresh()
    coordinator.dynamic[subentry.subentry_id] = dynamic

    @callback
    def _moved(event: Event) -> None:
        """Search again when the tracked entity has moved a while (or gets a position)."""
        new = position_of(event.data["new_state"])
        old = dynamic.position
        if new is None and old is None:
            return
        if new is None or old is None or distance_m(*old, *new) >= DYNAMIC_MOVE_M:
            hass.async_create_task(dynamic.async_request_refresh())

    entry.async_on_unload(
        async_track_state_change_event(hass, [dynamic.tracked_entity], _moved)
    )

    @callback
    def _label_device() -> None:
        """Name the device after the followed entity; link it to the best charger's device."""
        registry = dr.async_get(hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, f"dynamic_{subentry.subentry_id}"), entry.entry_id
        )
        if device is None:  # the entities have not been added yet
            return
        state = hass.states.get(dynamic.tracked_entity)
        followed = state.name if state else dynamic.tracked_entity
        results = dynamic.data["results"]
        best = results[0] if results else None
        target = (
            registry.async_get_device_by_identifier((DOMAIN, best["id"]), entry.entry_id)
            if best
            else None
        )
        registry.async_update_device(
            device.id,
            name=f"{subentry.title} · {followed}",
            via_device_id=target.id if target else None,
        )

    entry.async_on_unload(dynamic.async_add_listener(_label_device))
    return _label_device


async def async_unload_entry(hass: HomeAssistant, entry: NdwConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NdwConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Deleting a charging location's device removes it from its place."""
    ids = {i for domain, i in device_entry.identifiers if domain == DOMAIN}
    for identifier in ids:
        if identifier.startswith("dynamic_"):  # a dynamic charging location
            subentry_id = identifier.removeprefix("dynamic_")
            if subentry_id in entry.subentries:
                hass.config_entries.async_remove_subentry(entry, subentry_id)
                return True
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
