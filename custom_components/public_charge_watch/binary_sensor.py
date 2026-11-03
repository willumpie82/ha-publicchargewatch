"""Binary sensors per charging location: is a charge point free? (NDW only), and a diagnostic."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .const import DOMAIN
from .coordinator import NdwCoordinator
from .entity import NdwLocationEntity, add_location_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    _remove_source_sensor(hass, entry)

    def build(_subentry_id: str, location_id: str) -> list[NdwLocationEntity]:
        entities: list[NdwLocationEntity] = []
        if coordinator.live_occupancy:
            entities.append(NdwAnyAvailableBinarySensor(coordinator, location_id))
        if coordinator.can_be_rate_limited:
            entities.append(NdwRateLimitedBinarySensor(coordinator, location_id))
        return entities

    add_location_entities(entry, coordinator, async_add_entities, build)


def _remove_source_sensor(hass: HomeAssistant, entry: NdwConfigEntry) -> None:
    """v0.4.3 had one "rate limited" sensor on a device of the source; it is per location now."""
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_rate_limited"
    ):
        registry.async_remove(entity_id)
    devices = dr.async_get(hass)
    source = (DOMAIN, f"source_{entry.entry_id}")
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if source in device.identifiers:
            devices.async_remove_device(device.id)


class NdwAnyAvailableBinarySensor(NdwLocationEntity, BinarySensorEntity):
    """On when at least one charge point is available."""

    _attr_translation_key = "any_available"

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_any_available"

    @property
    def is_on(self) -> bool:
        return any(g["available"] > 0 for g in self.location["groups"])


class NdwRateLimitedBinarySensor(NdwLocationEntity, BinarySensorEntity):
    """On when the source refused (429) the last request for this location.

    The other entities of the location keep their last value for a while, or go unavailable;
    this one says why, and stays available itself.
    """

    _attr_translation_key = "rate_limited"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_rate_limited"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.location_id in self.coordinator.failed
