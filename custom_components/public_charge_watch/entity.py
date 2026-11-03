"""Base entities: one device per location, one sub-device per charge point."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DynamicCoordinator, NdwCoordinator, iter_locations


def add_location_entities(
    entry: ConfigEntry,
    coordinator: NdwCoordinator,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[str, str], list[Entity]],
) -> None:
    """Add build(subentry_id, location_id) for every location, as soon as its data is there.

    A location can be missing at startup (for instance one area was rate limited): its
    entities then appear with a later update, not only after a reload.
    """
    added: set[str] = set()

    def _add_new() -> None:
        for subentry_id, location_id in iter_locations(entry):
            if location_id in added or location_id not in coordinator.data:
                continue
            added.add(location_id)
            async_add_entities(build(subentry_id, location_id), config_subentry_id=subentry_id)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class NdwLocationEntity(CoordinatorEntity[NdwCoordinator]):
    """Entity attached to the charging location device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator)
        self.location_id = location_id
        loc = coordinator.data[location_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, location_id)},
            name=loc["address"] or location_id,  # the place is the subentry above it
            manufacturer=loc.get("operator"),
            model="Charging location",
        )

    @property
    def location(self) -> dict[str, Any]:
        return self.coordinator.data[self.location_id]

    @property
    def available(self) -> bool:
        return super().available and self.location_id in self.coordinator.data


class NdwEvseEntity(NdwLocationEntity):
    """Entity attached to a single charge point (EVSE) device."""

    def __init__(
        self, coordinator: NdwCoordinator, location_id: str, evse_id: str
    ) -> None:
        super().__init__(coordinator, location_id)
        self.evse_id = evse_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, evse_id)},
            name=evse_id,
            manufacturer=coordinator.data[location_id].get("operator"),
            model="Charge point",
            via_device=(DOMAIN, location_id),
        )

    @property
    def evse(self) -> dict[str, Any] | None:
        for evse in self.location["evses"]:
            if evse["evse_id"] == self.evse_id:
                return evse
        return None

    @property
    def available(self) -> bool:
        return super().available and self.evse is not None


class DynamicEntity(CoordinatorEntity[DynamicCoordinator]):
    """Entity of a dynamic charging location (the charger nearest to a tracked entity)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DynamicCoordinator, subentry_id: str) -> None:
        super().__init__(coordinator)
        self._subentry_id = subentry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"dynamic_{subentry_id}")},
            name=coordinator.subentry.title,
            model="Dynamic charging location",
        )

    @property
    def best(self) -> dict[str, Any] | None:
        results = self.coordinator.data["results"]
        return results[0] if results else None

    @property
    def best_attributes(self) -> dict[str, Any]:
        """The nearest charger as attributes, without coordinates (the tracker has those)."""
        best = self.best
        if best is None:
            return {}
        return {k: v for k, v in best.items() if k not in ("latitude", "longitude")}
