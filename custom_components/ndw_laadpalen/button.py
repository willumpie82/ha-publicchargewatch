"""Diagnostic button: refresh this location now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .coordinator import NdwCoordinator, iter_locations
from .entity import NdwLocationEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    for subentry_id, location_id in iter_locations(entry):
        if location_id in coordinator.data:
            async_add_entities(
                [NdwRefreshButton(coordinator, location_id)],
                config_subentry_id=subentry_id,
            )


class NdwRefreshButton(NdwLocationEntity, ButtonEntity):
    """Fetches fresh data now instead of waiting for the next update interval."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_force_refresh()
