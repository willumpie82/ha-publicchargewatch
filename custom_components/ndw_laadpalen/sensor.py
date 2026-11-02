"""Sensors. What gets created depends on the data the location actually has."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NdwConfigEntry
from .const import EVSE_STATUSES
from .coordinator import NdwCoordinator
from .entity import NdwEvseEntity, NdwLocationEntity
from .parser import group_label


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NdwAvailableSensor(coordinator)
            if coordinator.live_occupancy
            else NdwOperationalSensor(coordinator),
            NdwUpdatedSensor(coordinator),
        ]
    )

    known: set[str] = set()

    def _add_new() -> None:
        """Create entities for data that appeared (also called once at setup)."""
        data = coordinator.data
        new: list[SensorEntity] = []
        if coordinator.live_occupancy:
            if len(data["groups"]) > 1:  # with one group it would repeat the location total
                new += [
                    NdwGroupSensor(coordinator, g["key"])
                    for g in data["groups"]
                    if f"group_{g['key']}" not in known
                ]
                known.update(f"group_{g['key']}" for g in data["groups"])
        else:
            new += [
                NdwEvseStatusSensor(coordinator, e["evse_id"])
                for e in data["evses"]
                if e["evse_id"] not in known
            ]
            known.update(e["evse_id"] for e in data["evses"])
        if data.get("price_per_kwh") is not None and "price" not in known:
            known.add("price")
            new.append(NdwPriceSensor(coordinator))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def _location_attributes(loc: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_points": loc.get("points") or len(loc.get("evses", [])),
        "operator": loc.get("operator"),
        "address": loc.get("address"),
        "city": loc.get("city"),
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "last_updated": loc.get("last_updated"),
    }


class NdwAvailableSensor(NdwLocationEntity, SensorEntity):
    """NDW: number of free charge points at the location."""

    _attr_translation_key = "available_points"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NdwCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_available"

    @property
    def native_value(self) -> int:
        return sum(g["available"] for g in self.location["groups"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _location_attributes(self.location)


class NdwGroupSensor(NdwLocationEntity, SensorEntity):
    """NDW: free charge points of one connector group (e.g. 'Type 2 · 11 kW')."""

    _attr_translation_key = "group_available"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NdwCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.location_id}_{key}"
        group = self.group or {}
        self._attr_translation_placeholders = {"group": group_label(group)}

    @property
    def group(self) -> dict[str, Any] | None:
        return next((g for g in self.location["groups"] if g["key"] == self._key), None)

    @property
    def available(self) -> bool:
        return super().available and self.group is not None

    @property
    def native_value(self) -> int | None:
        return self.group["available"] if self.group else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if (group := self.group) is None:
            return {}
        return {
            "total": group["total"],
            "connector": group["connector_type"],
            "power_type": group["power_type"],
            "max_power_kw": round(group["power_w"] / 1000, 2) if group["power_w"] else None,
            "price_per_kwh": self.location.get("prices_by_group", {}).get(self._key),
        }


class NdwOperationalSensor(NdwLocationEntity, SensorEntity):
    """OCM: connections believed to work. Says nothing about occupancy."""

    _attr_translation_key = "operational_points"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NdwCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_operational"

    @property
    def native_value(self) -> int:
        return sum(
            e["connectors"][0].get("quantity", 1) if e["connectors"] else 1
            for e in self.location["evses"]
            if e["status"] == "OPERATIONAL"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _location_attributes(self.location)


class NdwEvseStatusSensor(NdwEvseEntity, SensorEntity):
    """OCM: status of one connection group."""

    _attr_translation_key = "evse_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = EVSE_STATUSES

    def __init__(self, coordinator: NdwCoordinator, evse_id: str) -> None:
        super().__init__(coordinator, evse_id)
        self._attr_unique_id = f"{evse_id}_status"

    @property
    def native_value(self) -> str | None:
        if (evse := self.evse) is None:
            return None
        status = evse["status"].lower()
        return status if status in EVSE_STATUSES else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if (evse := self.evse) is None:
            return {}
        return {
            "evse_id": evse["evse_id"],
            "last_updated": evse["last_updated"],
            "connectors": [
                {
                    "standard": c["standard"],
                    "power_type": c["power_type"],
                    "max_power_kw": round(c["max_power_w"] / 1000, 1)
                    if c["max_power_w"]
                    else None,
                    "quantity": c.get("quantity"),
                }
                for c in evse["connectors"]
            ],
        }


class NdwPriceSensor(NdwLocationEntity, SensorEntity):
    """Price per kWh: NDW tariff (incl. VAT, exact) or OCM free text (best effort)."""

    _attr_translation_key = "price_per_kwh"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator: NdwCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_price"

    @property
    def native_value(self) -> float | None:
        return self.location.get("price_per_kwh")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self.location
        details = loc.get("price_details") or {}
        return {
            "source_text": loc.get("price_text"),
            # tariff (NDW), per_kwh / assumed_per_kwh / free (OCM text), unknown
            "price_basis": loc.get("price_basis"),
            "vat_percent": details.get("vat"),
            "start_fee": details.get("start_fee"),
            "time_price_per_hour": details.get("time_price_per_hour"),
            "parking_price_per_hour": details.get("parking_price_per_hour"),
            # true when day/night or size-dependent rates exist beyond this headline price
            "has_conditional_rates": details.get("complex"),
            "tariff_updated": details.get("last_updated"),
        }


class NdwUpdatedSensor(NdwLocationEntity, SensorEntity):
    """When the source last updated this location."""

    _attr_translation_key = "last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NdwCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_updated"

    @property
    def native_value(self) -> datetime | None:
        if text := self.location.get("last_updated"):
            return dt_util.parse_datetime(text)
        return None
