"""Diagnostic button: refresh this location now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .coordinator import DynamicCoordinator, NdwCoordinator
from .entity import DynamicEntity, NdwLocationEntity, add_location_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    for subentry_id, dynamic in coordinator.dynamic.items():
        async_add_entities(
            [NdwNearestRefreshButton(dynamic, subentry_id, coordinator)],
            config_subentry_id=subentry_id,
        )
    add_location_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda _subentry_id, location_id: [NdwRefreshButton(coordinator, location_id)],
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


class NdwNearestRefreshButton(DynamicEntity, ButtonEntity):
    """Checks the NDW data again now and searches around the followed entity."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DynamicCoordinator,
        subentry_id: str,
        source: NdwCoordinator,
    ) -> None:
        super().__init__(coordinator, subentry_id)
        self._source = source
        self._attr_unique_id = f"{subentry_id}_nearest_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_force_refresh()
        await self._source.async_request_refresh()  # the locations of this source too
