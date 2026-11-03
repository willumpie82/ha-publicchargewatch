"""Event entity: a location became available / occupied (NDW only; OCM cannot tell)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NdwConfigEntry
from .const import CONF_NOTIFY
from .coordinator import DynamicCoordinator, NdwCoordinator
from .entity import DynamicEntity, NdwLocationEntity, add_location_entities

BECAME_AVAILABLE = "became_available"
BECAME_OCCUPIED = "became_occupied"


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    for subentry_id, dynamic in coordinator.dynamic.items():
        async_add_entities(
            [NdwNearestEvent(dynamic, subentry_id)], config_subentry_id=subentry_id
        )
    if not coordinator.live_occupancy:
        return

    def build(subentry_id: str, location_id: str) -> list[NdwAvailabilityEvent]:
        gate = entry.subentries[subentry_id].data.get(CONF_NOTIFY, {}).get(location_id)
        return [NdwAvailabilityEvent(coordinator, location_id, gate)]

    add_location_entities(entry, coordinator, async_add_entities, build)


class NdwAvailabilityEvent(NdwLocationEntity, EventEntity):
    """Fires when a location gets its first free charge point or loses its last one.

    When a notify entity is configured for the location, events only fire while that
    entity is on (e.g. a helper that says "I need to charge").
    """

    _attr_translation_key = "availability"
    _attr_event_types = [BECAME_AVAILABLE, BECAME_OCCUPIED]

    def __init__(
        self, coordinator: NdwCoordinator, location_id: str, notify_entity: str | None
    ) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_availability"
        self._notify_entity = notify_entity
        self._free = self._count_free()

    def _count_free(self) -> int | None:
        loc = self.coordinator.data.get(self.location_id)
        return sum(g["available"] for g in loc["groups"]) if loc else None

    @property
    def _gate_open(self) -> bool:
        if self._notify_entity is None:
            return True
        state = self.hass.states.get(self._notify_entity)
        return state is not None and state.state == "on"

    @callback
    def _handle_coordinator_update(self) -> None:
        free = self._count_free()
        if (
            free is not None
            and self._free is not None
            and (free > 0) != (self._free > 0)
            and self._gate_open
        ):
            loc = self.location
            self._trigger_event(
                BECAME_AVAILABLE if free > 0 else BECAME_OCCUPIED,
                {
                    "free": free,
                    "total": sum(g["total"] for g in loc["groups"]),
                    "address": loc["address"],
                },
            )
        if free is not None:
            self._free = free
        super()._handle_coordinator_update()


NEAREST_CHANGED = "nearest_changed"


class NdwNearestEvent(DynamicEntity, EventEntity):
    """Fires when a different charger with a free charge point becomes the best one."""

    _attr_translation_key = "nearest"
    _attr_event_types = [NEAREST_CHANGED]

    def __init__(self, coordinator: DynamicCoordinator, subentry_id: str) -> None:
        super().__init__(coordinator, subentry_id)
        self._attr_unique_id = f"{subentry_id}_nearest_event"
        self._last = self._free_best_id()

    def _free_best_id(self) -> str | None:
        best = self.best
        return best["id"] if best and best["free"] > 0 else None

    @callback
    def _handle_coordinator_update(self) -> None:
        current = self._free_best_id()
        if current is not None and current != self._last:
            self._trigger_event(NEAREST_CHANGED, self.best_attributes)
        self._last = current
        super()._handle_coordinator_update()
