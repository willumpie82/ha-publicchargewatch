"""Config flow.

One config entry per source (NDW or Open Charge Map); every charging location is a
subentry. The first one is chosen while setting up the entry:

source -> (OCM: API key | NDW: confirm the download) -> search method (zone / postcode /
map) -> search (progress) -> pick a charger -> update interval.

"Add charging location" on the entry repeats the search part for another location.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import NdwConfigEntry, get_client
from .const import (
    CONNECTOR_NAMES,
    CONF_API_KEY,
    CONF_CONNECTORS,
    CONF_HOUSE_NUMBER,
    CONF_LOCATION,
    CONF_CITY,
    CONF_COORDS,
    CONF_LOCATION_ID,
    CONF_LOCATION_IDS,
    CONF_MIN_POWER,
    CONF_NOTIFY,
    CONF_NOTIFY_ENTITY,
    CONF_ONLY_AVAILABLE,
    CONF_POSTCODE,
    CONF_RADIUS,
    CONF_LOOKUP_RADIUS,
    CONF_SCAN_INTERVAL,
    DOTNL_LOCATION_RADIUS_M,
    DOTNL_RADIUS_RANGE_M,
    CONF_SOURCE,
    CONF_TRACKED,
    CONF_ZONE,
    DEFAULT_DYNAMIC_RADIUS_M,
    DEFAULT_RADIUS_M,
    DOMAIN,
    MAX_DYNAMIC_RADIUS_M,
    MAX_RADIUS_M,
    OCM_API_URL,
    SOURCE_NDW,
    SOURCE_OCM,
    SUBENTRY_DYNAMIC,
    SUBENTRY_LOCATION,
)
from .geocoding import async_geocode_postcode, async_reverse_city, normalize_postcode
from .providers import PROVIDERS
from .providers.base import ProviderAuthError
from .providers.ndw_api import NdwApi

_NETWORK_ERRORS = (aiohttp.ClientError, TimeoutError)


def _place_key(city: str | None) -> str:
    return (city or "").strip().lower() or "other"


def _place_data(
    city: str | None, ids: list[str], coords: dict[str, list[float]]
) -> dict[str, Any]:
    return {CONF_CITY: city, CONF_LOCATION_IDS: ids, CONF_COORDS: coords}


def _title(loc: dict[str, Any]) -> str:
    return ", ".join(p for p in (loc.get("address"), loc.get("city")) if p) or loc["id"]


def _radius_field() -> dict[Any, Any]:
    return {
        vol.Required(CONF_RADIUS, default=DEFAULT_RADIUS_M): NumberSelector(
            NumberSelectorConfig(
                min=50,
                max=MAX_RADIUS_M,
                step=25,
                unit_of_measurement="m",
                mode=NumberSelectorMode.SLIDER,
            )
        )
    }


def _key_schema(default: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_API_KEY, default=default): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            )
        }
    )


def _describe(loc: dict[str, Any]) -> str:
    """One line for the picker: name, operator, availability, power, price, distance."""
    parts = [_title(loc), loc.get("operator") or "?"]
    if "groups" in loc:  # NDW: live availability
        free = sum(g["available"] for g in loc["groups"])
        parts.append(f"{free}/{loc['points']} ✓")  # language-neutral: free of total
        if powers := [g["power_w"] for g in loc["groups"] if g["power_w"]]:
            parts.append(f"{round(max(powers) / 1000, 1):g} kW")
    else:  # OCM
        parts.append(f"{loc.get('points') or len(loc['evses'])}×")
        if powers := [c["max_power_w"] for e in loc["evses"] for c in e["connectors"] if c["max_power_w"]]:
            parts.append(f"{round(max(powers) / 1000, 1):g} kW")
    if (price := loc.get("price_per_kwh")) is not None:
        parts.append(f"€{price:g}/kWh" if price == 0 else f"€{price:.4f}/kWh")
    parts.append(f"{loc['distance']} m")
    return " · ".join(parts)


_FLOW_FEED_AGE = 600  # seconds: a search may reuse a recent NDW download


class _LocationSearch:
    """Steps shared by the entry flow and the "add charging location" subentry flow."""

    hass: Any
    _auth_step = "location_method"  # where to go when the OCM key is rejected

    def _init_search(self, source: str, api_key: str | None) -> None:
        self._source = source
        self._api_key = api_key
        self._task: asyncio.Task | None = None
        self._search: tuple[float, float, float] | None = None
        self._candidates: dict[str, dict[str, Any]] = {}
        self._problem = "cannot_connect"
        self._problem_next = "location_method"
        self._after_fetch = "location_method"  # step that follows the download
        self._hidden = 0

    @property
    def _client_data(self) -> dict[str, Any]:
        return {CONF_SOURCE: self._source, CONF_API_KEY: self._api_key}

    @property
    def _known_ids(self) -> set[str]:
        """Locations that are already added (hidden from the picker)."""
        return set()

    def _hidden_note(self) -> str:
        if not self._hidden:
            return ""
        if self.hass.config.language.startswith("nl"):
            return f"\n\n_{self._hidden} al toegevoegde laadlocatie(s) staan niet in de lijst._"
        return f"\n\n_{self._hidden} already added location(s) are not listed._"

    async def _locations_chosen(self, locs: list[dict[str, Any]]) -> Any:
        raise NotImplementedError

    async def _group_by_place(
        self, locs: list[dict[str, Any]]
    ) -> dict[str, tuple[str | None, list[str], dict[str, list[float]]]]:
        """Chosen locations grouped per place: {place key: (town, ids, coordinates)}."""
        groups: dict[str, tuple[str | None, list[str], dict[str, list[float]]]] = {}
        for loc in locs:
            city = await self._place_of(loc)
            _, ids, coords = groups.setdefault(_place_key(city), (city, [], {}))
            ids.append(loc["id"])
            coords[loc["id"]] = [loc["latitude"], loc["longitude"]]
        return groups

    async def _place_of(self, loc: dict[str, Any]) -> str | None:
        """Town of a location; NDW has none in its feed, so ask the PDOK geocoder."""
        if city := loc.get("city"):
            return city
        if not PROVIDERS[self._source].city_from_geocoder:
            return None  # the geocoder only knows the Netherlands
        try:
            return await async_reverse_city(
                async_get_clientsession(self.hass), loc["latitude"], loc["longitude"]
            )
        except _NETWORK_ERRORS:
            return None

    @callback
    def async_remove(self) -> None:
        task = getattr(self, "_task", None)  # absent when the flow never reached a search
        if task and not task.done():
            task.cancel()

    # --- NDW: download the feed (progress screen) ----------------------------------

    async def async_step_fetch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Download the NDW feed (progress screen)."""
        if self._task is None:
            self._task = self.hass.async_create_task(
                get_client(self.hass, self._client_data).async_prepare(
                    max_age=_FLOW_FEED_AGE
                )
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="fetch", progress_action="fetch", progress_task=self._task
            )
        task, self._task = self._task, None
        try:
            task.result()  # the shared feed keeps the download
        except _NETWORK_ERRORS:
            return self._fail("cannot_connect", "fetch")
        next_step, self._after_fetch = self._after_fetch, "location_method"
        return self.async_show_progress_done(next_step_id=next_step)

    # --- 3. search method ----------------------------------------------------------

    async def async_step_location_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = ["zone", "map"]
        if self.hass.config.country in (None, "NL"):  # postcodes are Dutch
            options.insert(1, "postcode")
        return self.async_show_menu(step_id="location_method", menu_options=options)

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            attrs = getattr(self.hass.states.get(user_input[CONF_ZONE]), "attributes", {})
            if "latitude" in attrs and "longitude" in attrs:
                return await self._start_search(
                    attrs["latitude"], attrs["longitude"], user_input[CONF_RADIUS]
                )
            errors[CONF_ZONE] = "zone_not_found"
        schema = vol.Schema(
            {
                vol.Required(CONF_ZONE, default="zone.home"): EntitySelector(
                    EntitySelectorConfig(domain="zone")
                ),
                **_radius_field(),
            }
        )
        return self.async_show_form(step_id="zone", data_schema=schema, errors=errors)

    async def async_step_postcode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if normalize_postcode(user_input[CONF_POSTCODE]) is None:
                errors[CONF_POSTCODE] = "postcode_invalid"
            else:
                try:
                    point = await async_geocode_postcode(
                        async_get_clientsession(self.hass),
                        user_input[CONF_POSTCODE],
                        user_input.get(CONF_HOUSE_NUMBER),
                    )
                except _NETWORK_ERRORS:
                    errors["base"] = "cannot_connect"
                else:
                    if point is None:
                        errors[CONF_POSTCODE] = "postcode_not_found"
                    else:
                        return await self._start_search(*point, user_input[CONF_RADIUS])
        schema = vol.Schema(
            {
                vol.Required(CONF_POSTCODE): TextSelector(),
                vol.Optional(CONF_HOUSE_NUMBER): TextSelector(),
                **_radius_field(),
            }
        )
        return self.async_show_form(step_id="postcode", data_schema=schema, errors=errors)

    async def async_step_map(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            loc = user_input[CONF_LOCATION]
            return await self._start_search(
                loc["latitude"],
                loc["longitude"],
                min(loc.get("radius", DEFAULT_RADIUS_M), MAX_RADIUS_M),
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOCATION,
                    default={
                        "latitude": self.hass.config.latitude,
                        "longitude": self.hass.config.longitude,
                        "radius": DEFAULT_RADIUS_M,
                    },
                ): LocationSelector(LocationSelectorConfig(radius=True))
            }
        )
        return self.async_show_form(step_id="map", data_schema=schema)

    # --- 4. search (progress) ------------------------------------------------------

    async def _start_search(
        self, latitude: float, longitude: float, radius: float
    ) -> ConfigFlowResult:
        self._search = (float(latitude), float(longitude), float(radius))
        self._task = None
        return await self.async_step_search()

    async def _run_search(self) -> list[dict[str, Any]]:
        assert self._search is not None
        client = get_client(self.hass, self._client_data)
        return await client.async_nearby_live(*self._search, max_age=_FLOW_FEED_AGE)

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            self._task is None
            and PROVIDERS[self._source].bulk_download
            and not get_client(self.hass, self._client_data).is_fresh(_FLOW_FEED_AGE)
        ):
            # the held feed is too old: show the download as its own progress step
            self._after_fetch = "search"
            return await self.async_step_fetch()
        if self._task is None:
            self._task = self.hass.async_create_task(self._run_search())
        if not self._task.done():
            return self.async_show_progress(
                step_id="search", progress_action="search", progress_task=self._task
            )
        task, self._task = self._task, None
        try:
            found = task.result()
        except ProviderAuthError:
            return self._fail("invalid_auth", self._auth_step)
        except _NETWORK_ERRORS:
            return self._fail("cannot_connect", "location_method")
        if not found:
            return self._fail("no_locations", "location_method")
        self._hidden = sum(1 for c in found if c["id"] in self._known_ids)
        found = [c for c in found if c["id"] not in self._known_ids]
        if not found:
            return self._fail("no_locations", "location_method")
        self._candidates = {c["id"]: c for c in found}
        return self.async_show_progress_done(next_step_id="select")

    def _fail(self, error: str, next_step: str) -> ConfigFlowResult:
        """Leave a progress screen with an error; the user retries from next_step."""
        self._problem, self._problem_next = error, next_step
        return self.async_show_progress_done(next_step_id="problem")

    async def async_step_problem(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input == {}:  # the (empty) form was submitted; leftovers are never empty
            return await getattr(self, f"async_step_{self._problem_next}")()
        return self.async_show_form(
            step_id="problem",
            data_schema=vol.Schema({}),
            errors={"base": self._problem},
        )

    # --- 5. pick a charger ---------------------------------------------------------

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # After a progress screen HA hands this step the previous form's input, so only
        # react to our own field.
        errors: dict[str, str] = {}
        if user_input is not None and CONF_LOCATION_IDS in user_input:
            if chosen := user_input[CONF_LOCATION_IDS]:
                return await self._locations_chosen(
                    [self._candidates[i] for i in chosen]
                )
            errors["base"] = "no_selection"

        options = [
            SelectOptionDict(value=c["id"], label=_describe(c))
            for c in self._candidates.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_LOCATION_IDS, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "hidden": self._hidden_note()
            },
        )


class NdwConfigFlow(_LocationSearch, ConfigFlow, domain=DOMAIN):
    """Set up a source; its first charging location is chosen right away."""

    VERSION = 1
    _auth_step = "api_key"

    def __init__(self) -> None:
        self._init_search(SOURCE_NDW, None)
        self._places_chosen: dict[str, tuple[str | None, list[str], dict[str, list[float]]]] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NdwOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        types: dict[str, type[ConfigSubentryFlow]] = {SUBENTRY_LOCATION: LocationSubentryFlow}
        if PROVIDERS[config_entry.data.get(CONF_SOURCE, SOURCE_NDW)].live_occupancy:  # needs live data
            types[SUBENTRY_DYNAMIC] = DynamicSubentryFlow
        return types

    # --- 1. source -----------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._source = user_input[CONF_SOURCE]
            await self.async_set_unique_id(self._source)
            self._abort_if_unique_id_configured()  # add locations to that entry instead
            if PROVIDERS[self._source].needs_api_key:
                return await self.async_step_api_key()
            if PROVIDERS[self._source].bulk_download:
                return await self.async_step_ndw_confirm()
            return await self.async_step_location_method()
        configured = {e.unique_id for e in self._async_current_entries()}
        options = [s for s in PROVIDERS if s not in configured]
        if not options:
            return self.async_abort(reason="already_configured")
        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE, default=options[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        translation_key=CONF_SOURCE,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    # --- 2a. OCM: API key ----------------------------------------------------------

    async def async_step_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input[CONF_API_KEY].strip()
            try:
                await get_client(self.hass, self._client_data).async_validate()
            except ProviderAuthError:
                errors["base"] = "invalid_auth"
            except _NETWORK_ERRORS:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_location_method()
        return self.async_show_form(
            step_id="api_key",
            data_schema=_key_schema(self._api_key or ""),
            description_placeholders={"api_url": OCM_API_URL},
            errors=errors,
        )

    # --- 2b. NDW: explain and confirm the download, then fetch it -------------------

    async def async_step_ndw_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="ndw_confirm", menu_options=["fetch", "user"]
        )

    async def _locations_chosen(self, locs: list[dict[str, Any]]) -> ConfigFlowResult:
        self._places_chosen = await self._group_by_place(locs)
        provider = PROVIDERS[self._source]
        if provider.bulk_download:
            return await self.async_step_interval_ndw()
        if provider.live_occupancy:
            return await self.async_step_interval_api()
        return await self.async_step_interval_ocm()

    # --- 6. update interval --------------------------------------------------------

    async def async_step_interval_ndw(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._interval_step("interval_ndw", user_input)

    async def async_step_interval_api(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._interval_step("interval_api", user_input)

    async def async_step_interval_ocm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._interval_step("interval_ocm", user_input)

    async def _interval_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        assert self._places_chosen
        default, minimum = PROVIDERS[self._source].scan_interval
        if user_input is not None:
            data = {CONF_SOURCE: self._source}
            if PROVIDERS[self._source].needs_api_key:
                data[CONF_API_KEY] = self._api_key
            return self.async_create_entry(
                title=PROVIDERS[self._source].name,
                data=data,
                options={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])},
                subentries=[
                    ConfigSubentryData(
                        subentry_type=SUBENTRY_LOCATION,
                        title=city or "Other",
                        data=_place_data(city, ids, coords),
                        unique_id=key,
                    )
                    for key, (city, ids, coords) in self._places_chosen.items()
                ],
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=_interval_schema(default, default, minimum),
            description_placeholders={"minimum": str(minimum)},
        )

    # --- re-authentication (OCM key rejected) --------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            try:
                await get_client(
                    self.hass, {CONF_SOURCE: SOURCE_OCM, CONF_API_KEY: key}
                ).async_validate()
            except ProviderAuthError:
                errors["base"] = "invalid_auth"
            except _NETWORK_ERRORS:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_key_schema(),
            description_placeholders={"api_url": OCM_API_URL},
            errors=errors,
        )


def _interval_schema(current: int, default: int, minimum: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                NumberSelectorConfig(
                    min=minimum,
                    max=86400,
                    step=30,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            )
        }
    )


class NdwOptionsFlow(OptionsFlow):
    """Change the update interval later."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        source = self.config_entry.data.get(CONF_SOURCE, SOURCE_NDW)
        default, minimum = PROVIDERS[source].scan_interval
        if user_input is not None:
            data = {CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            if CONF_LOOKUP_RADIUS in user_input:
                data[CONF_LOOKUP_RADIUS] = int(user_input[CONF_LOOKUP_RADIUS])
            return self.async_create_entry(data=data)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, default)
        schema = _interval_schema(current, default, minimum)
        if source == NdwApi.provider_id:
            radius = self.config_entry.options.get(CONF_LOOKUP_RADIUS, DOTNL_LOCATION_RADIUS_M)
            schema = schema.extend(
                {
                    vol.Required(CONF_LOOKUP_RADIUS, default=radius): NumberSelector(
                        NumberSelectorConfig(
                            min=DOTNL_RADIUS_RANGE_M[0],
                            max=DOTNL_RADIUS_RANGE_M[1],
                            step=10,
                            unit_of_measurement="m",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            )
        return self.async_show_form(step_id="init", data_schema=schema)


class LocationSubentryFlow(_LocationSearch, ConfigSubentryFlow):
    """"Add charging location" on an existing source."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        self._init_search(entry.data[CONF_SOURCE], entry.data.get(CONF_API_KEY))
        return await self.async_step_location_method()

    @property
    def _known_ids(self) -> set[str]:
        return {i for sub in self._places() for i in sub.data[CONF_LOCATION_IDS]}

    def _places(self) -> list[Any]:
        return [
            sub
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_LOCATION
        ]

    async def _locations_chosen(self, locs: list[dict[str, Any]]) -> SubentryFlowResult:
        """Add each charger to the place it is in, or start a new place."""
        groups = await self._group_by_place(locs)
        entry = self._get_entry()
        existing = {place.unique_id: place for place in self._places()}
        new = {k: v for k, v in groups.items() if k not in existing}
        if len(groups) == 1 and len(new) == 1:
            (key, (city, ids, coords)), = new.items()
            return self.async_create_entry(
                title=city or "Other", data=_place_data(city, ids, coords), unique_id=key
            )
        for key, (city, ids, coords) in groups.items():
            if place := existing.get(key):
                self.hass.config_entries.async_update_subentry(
                    entry,
                    place,
                    data={
                        **place.data,
                        CONF_LOCATION_IDS: [*place.data[CONF_LOCATION_IDS], *ids],
                        CONF_COORDS: {**place.data.get(CONF_COORDS, {}), **coords},
                    },
                )
            else:
                self.hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(_place_data(city, ids, coords)),
                        subentry_type=SUBENTRY_LOCATION,
                        title=city or "Other",
                        unique_id=key,
                    ),
                )
        names = ", ".join(city or "Other" for city, _, _ in groups.values())
        return self.async_abort(
            reason="location_added", description_placeholders={"place": names}
        )

    # --- reconfigure: choose a notify entity per location ---------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        place = self._get_reconfigure_subentry()
        ids = place.data[CONF_LOCATION_IDS]
        if user_input is not None and CONF_LOCATION_ID in user_input:
            self._configure_id = user_input[CONF_LOCATION_ID]
            return await self.async_step_notify()
        if len(ids) == 1:
            self._configure_id = ids[0]
            return await self.async_step_notify()
        coordinator = getattr(self._get_entry(), "runtime_data", None)
        names = {
            i: (coordinator.data.get(i, {}).get("address") if coordinator else None) or i
            for i in ids
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_LOCATION_ID, default=ids[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=i, label=names[i]) for i in ids],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema)

    async def async_step_notify(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        place = self._get_reconfigure_subentry()
        notify = dict(place.data.get(CONF_NOTIFY, {}))
        if user_input is not None:
            if entity := user_input.get(CONF_NOTIFY_ENTITY):
                notify[self._configure_id] = entity
            else:
                notify.pop(self._configure_id, None)
            return self.async_update_and_abort(
                self._get_entry(), place, data_updates={CONF_NOTIFY: notify}
            )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NOTIFY_ENTITY,
                    description={"suggested_value": notify.get(self._configure_id)},
                ): EntitySelector(
                    EntitySelectorConfig(domain=["binary_sensor", "input_boolean"])
                )
            }
        )
        return self.async_show_form(step_id="notify", data_schema=schema)


class DynamicSubentryFlow(ConfigSubentryFlow):
    """"Add dynamic charging location": the charger nearest to a tracked entity."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            name, data = user_input[CONF_NAME], {
                k: v for k, v in user_input.items() if k != CONF_NAME
            }
            data[CONF_RADIUS] = int(data[CONF_RADIUS])
            return self.async_create_entry(title=name, data=data)
        return self.async_show_form(step_id="user", data_schema=_dynamic_schema({}, None))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            name, data = user_input[CONF_NAME], {
                k: v for k, v in user_input.items() if k != CONF_NAME
            }
            data[CONF_RADIUS] = int(data[CONF_RADIUS])
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=name, data=data
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_dynamic_schema(dict(subentry.data), subentry.title),
        )


def _dynamic_schema(current: dict[str, Any], title: str | None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=title or "Nearest charger"): TextSelector(),
            vol.Required(
                CONF_TRACKED, default=current.get(CONF_TRACKED, vol.UNDEFINED)
            ): EntitySelector(
                EntitySelectorConfig(domain=["person", "device_tracker", "zone"])
            ),
            vol.Required(
                CONF_RADIUS, default=current.get(CONF_RADIUS, DEFAULT_DYNAMIC_RADIUS_M)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=250,
                    max=MAX_DYNAMIC_RADIUS_M,
                    step=250,
                    unit_of_measurement="m",
                    mode=NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_CONNECTORS,
                default=current.get(CONF_CONNECTORS, ["IEC_62196_T2", "IEC_62196_T2_COMBO"]),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v) for k, v in CONNECTOR_NAMES.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_MIN_POWER, default=current.get(CONF_MIN_POWER, 0)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=350,
                    step=1,
                    unit_of_measurement="kW",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_ONLY_AVAILABLE, default=current.get(CONF_ONLY_AVAILABLE, True)
            ): BooleanSelector(),
        }
    )
