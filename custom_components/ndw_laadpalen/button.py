"""Diagnostic button: refresh this location now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .coordinator import NdwCoordinator
from .entity import NdwLocationEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([NdwRefreshButton(entry.runtime_data)])


class NdwRefreshButton(NdwLocationEntity, ButtonEntity):
    """Fetches fresh data now instead of waiting for the next update interval."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NdwCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_force_refresh()
