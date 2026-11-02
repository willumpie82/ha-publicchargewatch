"""Binary sensor: is at least one charge point free? (NDW only; OCM cannot tell.)"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .coordinator import NdwCoordinator
from .entity import NdwLocationEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.runtime_data.live_occupancy:
        async_add_entities([NdwAnyAvailableBinarySensor(entry.runtime_data)])


class NdwAnyAvailableBinarySensor(NdwLocationEntity, BinarySensorEntity):
    """On when at least one charge point is available."""

    _attr_translation_key = "any_available"

    def __init__(self, coordinator: NdwCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_any_available"

    @property
    def is_on(self) -> bool:
        return any(g["available"] > 0 for g in self.location["groups"])
