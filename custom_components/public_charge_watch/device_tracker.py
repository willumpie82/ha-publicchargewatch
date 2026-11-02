"""Device tracker: where the best charger around a tracked entity is (a navigation target)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .coordinator import DynamicCoordinator
from .entity import DynamicEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    for subentry_id, dynamic in entry.runtime_data.dynamic.items():
        async_add_entities(
            [NdwNearestTracker(dynamic, subentry_id)], config_subentry_id=subentry_id
        )


class NdwNearestTracker(DynamicEntity, TrackerEntity):
    """At the best charger; unknown when none qualifies, so it never points at a stale one."""

    _attr_translation_key = "nearest"

    def __init__(self, coordinator: DynamicCoordinator, subentry_id: str) -> None:
        super().__init__(coordinator, subentry_id)
        self._attr_unique_id = f"{subentry_id}_nearest_tracker"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self.best["latitude"] if self.best else None

    @property
    def longitude(self) -> float | None:
        return self.best["longitude"] if self.best else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.best_attributes
