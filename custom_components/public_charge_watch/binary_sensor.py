"""Binary sensor: is at least one charge point free? (NDW only; OCM cannot tell.)"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .const import DOMAIN
from .coordinator import NdwCoordinator, iter_locations
from .entity import NdwLocationEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.can_be_rate_limited:
        async_add_entities([NdwRateLimitedBinarySensor(coordinator, entry)])
    if not coordinator.live_occupancy:
        return
    for subentry_id, location_id in iter_locations(entry):
        if location_id in coordinator.data:
            async_add_entities(
                [NdwAnyAvailableBinarySensor(coordinator, location_id)],
                config_subentry_id=subentry_id,
            )


class NdwAnyAvailableBinarySensor(NdwLocationEntity, BinarySensorEntity):
    """On when at least one charge point is available."""

    _attr_translation_key = "any_available"

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_any_available"

    @property
    def is_on(self) -> bool:
        return any(g["available"] > 0 for g in self.location["groups"])


class NdwRateLimitedBinarySensor(CoordinatorEntity[NdwCoordinator], BinarySensorEntity):
    """On when the source answered the last request with "too many requests" (429)."""

    _attr_has_entity_name = True
    _attr_translation_key = "rate_limited"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NdwCoordinator, entry: NdwConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rate_limited"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"source_{entry.entry_id}")},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.rate_limited
