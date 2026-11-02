"""Binary sensor: is at least one charge point free? (NDW only; OCM cannot tell.)"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .coordinator import NdwCoordinator, iter_locations
from .entity import NdwLocationEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
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
