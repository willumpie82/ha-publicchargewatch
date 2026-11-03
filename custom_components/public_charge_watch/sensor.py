"""Sensors. What gets created depends on the data the location actually has."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NdwConfigEntry
from .const import EVSE_STATUSES
from .coordinator import DynamicCoordinator, NdwCoordinator, iter_locations
from .entity import DynamicEntity, NdwEvseEntity, NdwLocationEntity
from .providers.ndw_geojson import group_label


async def async_setup_entry(
    hass: HomeAssistant, entry: NdwConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    for subentry_id, dynamic in coordinator.dynamic.items():
        async_add_entities(
            [
                NdwNearestSensor(dynamic, subentry_id),
                NdwNearestDistanceSensor(dynamic, subentry_id),
                NdwNearestConnectorSensor(dynamic, subentry_id),
                NdwNearestFreeSensor(dynamic, subentry_id),
                NdwNearestPowerSensor(dynamic, subentry_id),
                NdwNearestUpdatedSensor(dynamic, subentry_id),
            ],
            config_subentry_id=subentry_id,
        )
    for subentry_id, location_id in iter_locations(entry):
        _setup_location(entry, coordinator, location_id, subentry_id, async_add_entities)


def _setup_location(
    entry: NdwConfigEntry,
    coordinator: NdwCoordinator,
    location_id: str,
    subentry_id: str,
    async_add_entities: AddEntitiesCallback,
) -> None:
    known: set[str] = set()

    def _add_main() -> None:
        if "main" in known or location_id not in coordinator.data:
            return
        known.add("main")
        async_add_entities(
            [
                NdwAvailableSensor(coordinator, location_id)
                if coordinator.live_occupancy
                else NdwOperationalSensor(coordinator, location_id),
                NdwUpdatedSensor(coordinator, location_id),
                NdwCheckedSensor(coordinator, location_id),
            ],
            config_subentry_id=subentry_id,
        )

    def _add_new() -> None:
        """Create entities for data that appeared (also called once at setup)."""
        _add_main()
        data = coordinator.data.get(location_id)
        if data is None:
            return
        new: list[SensorEntity] = []
        if coordinator.live_occupancy:
            if len(data["groups"]) > 1:  # with one group it would repeat the location total
                new += [
                    NdwGroupSensor(coordinator, location_id, g["key"])
                    for g in data["groups"]
                    if f"group_{g['key']}" not in known
                ]
                known.update(f"group_{g['key']}" for g in data["groups"])
        else:
            new += [
                NdwEvseStatusSensor(coordinator, location_id, e["evse_id"])
                for e in data["evses"]
                if e["evse_id"] not in known
            ]
            known.update(e["evse_id"] for e in data["evses"])
        if data.get("price_per_kwh") is not None and "price" not in known:
            known.add("price")
            new.append(NdwPriceSensor(coordinator, location_id))
        if new:
            async_add_entities(new, config_subentry_id=subentry_id)

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

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_available"

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

    def __init__(self, coordinator: NdwCoordinator, location_id: str, key: str) -> None:
        super().__init__(coordinator, location_id)
        self._key = key
        self._attr_unique_id = f"{location_id}_{key}"
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

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_operational"

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

    def __init__(
        self, coordinator: NdwCoordinator, location_id: str, evse_id: str
    ) -> None:
        super().__init__(coordinator, location_id, evse_id)
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

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_price"

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

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_updated"

    @property
    def native_value(self) -> datetime | None:
        if text := self.location.get("last_updated"):
            return dt_util.parse_datetime(text)
        return None


class NdwCheckedSensor(NdwLocationEntity, SensorEntity):
    """When we last fetched this location successfully; stale means updates are failing."""

    _attr_translation_key = "last_checked"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NdwCoordinator, location_id: str) -> None:
        super().__init__(coordinator, location_id)
        self._attr_unique_id = f"{location_id}_checked"

    @property
    def available(self) -> bool:
        return True  # must stay readable while updates fail

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update_ok": self.coordinator.last_update_success}


class NdwNearestSensor(DynamicEntity, SensorEntity):
    """Address of the best charger around the tracked entity; the details are attributes."""

    _attr_translation_key = "nearest"

    def __init__(self, coordinator: DynamicCoordinator, subentry_id: str) -> None:
        super().__init__(coordinator, subentry_id)
        self._attr_unique_id = f"{subentry_id}_nearest"

    @property
    def native_value(self) -> str | None:
        best = self.best
        return best["address"] if best else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **self.best_attributes,
            "results": [
                {k: v for k, v in r.items() if k not in ("latitude", "longitude")}
                for r in self.coordinator.data["results"]
            ],
        }


class _NearestDetail(DynamicEntity, SensorEntity):
    """One detail of the best charger; unknown when there is none."""

    _key: str

    def __init__(self, coordinator: DynamicCoordinator, subentry_id: str) -> None:
        super().__init__(coordinator, subentry_id)
        self._attr_translation_key = f"nearest_{self._key}"
        self._attr_unique_id = f"{subentry_id}_nearest_{self._key}"


class NdwNearestDistanceSensor(_NearestDetail):
    """Distance from the followed entity to the best charger."""

    _key = "distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.best["distance_m"] if self.best else None


class NdwNearestConnectorSensor(_NearestDetail):
    """Plug types of the best charger that match the filter, e.g. "Type 2"."""

    _key = "connector"

    @property
    def native_value(self) -> str | None:
        return ", ".join(self.best["connectors"]) if self.best else None


class NdwNearestFreeSensor(_NearestDetail):
    """Free charge points of the best charger (of the matching plug types)."""

    _key = "free"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.best["free"] if self.best else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not (best := self.best):
            return {}
        return {"total": best["total"], "free_of_total": f"{best['free']}/{best['total']}"}


class NdwNearestPowerSensor(_NearestDetail):
    """Highest power of the best charger's matching charge points."""

    _key = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    @property
    def native_value(self) -> float | None:
        return self.best["power_kw"] if self.best else None


class NdwNearestUpdatedSensor(_NearestDetail):
    """When the operator last updated the best charger (shown as "11 minutes ago")."""

    _key = "updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        if self.best and (text := self.best.get("last_updated")):
            return dt_util.parse_datetime(text)
        return None
