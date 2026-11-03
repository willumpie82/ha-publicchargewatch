# PublicChargeWatch — contributor notes

Home Assistant custom integration (HACS) showing public EV chargers as devices. Domain
`public_charge_watch`, code in `custom_components/public_charge_watch/`. User docs are in `README.md` and `docs/` (setup, entities, dynamic, examples, development); this
file is what you need to change the code safely.

## Structure
**Source** (config entry: NDW or Open Charge Map, `unique_id` = `ndw`/`ocm`) → **place** (config
subentry per town, type `location`, data `{city, location_ids[]}`, `unique_id` = lowercased town)
→ **charging location** (one device named by its address, linked to its subentry via
`config_subentry_id`). One `NdwCoordinator` per entry fetches all locations (`data` = dict by
location id); entities take `(coordinator, location_id)` and go unavailable when their location
is missing from the data.

| File | Role |
|---|---|
| `providers/` | one module per data source, prefixed with its source. `base.py`: the `Provider` interface (`async_nearby`, `async_get_locations`, capability flags like `live_occupancy`, `bulk_download`, `scan_interval`) and its errors; `__init__.py`: the `PROVIDERS` registry by id. NDW: `ndw_download.py` (`NdwFeed`, the bulk file: raw feed in memory, ETag, lock), `ndw_api.py` (`NdwApi`, bounding-box API), `ndw_shared.py` (download helper, `NdwTariffs` daily tariff store, price attachment), `ndw_geojson.py` and `ndw_tariffs.py` (Home-Assistant-free parsers). OCM: `ocm.py` (`OcmClient`) and `ocm_tariff.py` (price text parser). New sources subclass `Provider` and are added to `PROVIDERS`. Code must ask a provider for a capability, never compare source names |
| `geo.py`, `geocoding.py`, `dynamic_location.py` | `geo.py`: distance; `geocoding.py`: PDOK postcode / town lookups; `dynamic_location.py`: `pick()` for the dynamic location |
| `coordinator.py` | polling, `iter_locations(entry)`, force refresh, `last_success` |
| `config_flow.py` | `_LocationSearch` mixin (search steps) shared by `NdwConfigFlow` and `LocationSubentryFlow`; options flow = update interval |
| `sensor.py` `binary_sensor.py` `button.py` `entity.py` | entities, created from the data a location actually has |
| `DynamicCoordinator` (`coordinator.py`) | dynamic charging location (subentry type `dynamic`, NDW only): `pick()` ranks nearby chargers by plug / power / free; the coordinator follows a tracked entity (state listener in `__init__.py`, recompute after ~150 m); sensor, `device_tracker` and event entities live on its own device (`dynamic_<subentry_id>`) |

## Data sources (the non-obvious parts)
- **NDW** (NL only, live availability per connector group, exact tariffs): GeoJSON
  `charging_point_locations.geojson.gz` (5 MB gz / 41 MB, ~81k features, no city/postcode) and
  OCPI `charging_point_tariffs_ocpi.json.gz` (4 MB, ~105k tariffs; price is excl. VAT, `vat` in %,
  the integration shows incl. VAT). There is no regional filter, so every refresh downloads the
  whole feed (~1.4 GB/day at 5 min): all entries share one download (`max_age` = 80% of the
  interval), HTTP 304 when unchanged. Tariffs: at most once a day.
- **Open Charge Map**: API key (header `X-API-Key`), no live occupancy (status 50 = "last editor
  said it works", not "free"). Prices are free text; `providers/ocm_tariff.py` parses them conservatively
  ("0.00 jaarabonnement" is an import placeholder = unknown, never free).
- **PDOK Locatieserver** (no key): postcode → point (`free`, must verify the returned postcode
  because matching is fuzzy) and point → town (`reverse`, NDW has no city).
- Not everything exists: private/semi-public chargers (e.g. company sites) are in neither NDW nor
  OCM. That is a data limit, not a bug.

## NDW DOT-NL bounding-box API (`providers/ndw_api.py`, provider `ndw_api`)
`https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/charge-point-data/v1/features?bbox=w,s,e,n`
returns the same features as the bulk file for one area (no key; max 1 square degree; ~9 requests/s).
Things found by testing it live: there is **no lookup by id** (so places store `coordinates` per
location and known locations are fetched through a box around them, 150 m by default, option `lookup_radius`); answers above 1000
locations are paged and the `Link: rel="next"` URL has a **wrong path** (404): only its `cursor` is
usable, on the normal endpoint; multi-page answers come up a few locations short of
`x-total-count` (tolerated up to 1%, single pages must be complete); a 429 (seen about once in 8 refreshes, even at low volume, so it looks like a limit on NDW's side) is retried once after a short wait, and a coordinator keeps its last data for up to 3 update intervals before going unavailable (`ProviderRateLimitError`). Tariffs are shared with the bulk provider (`NdwTariffs`, one daily download).

## Performance rule: never build objects for the whole feed
`ndw_geojson.scan` and `ndw_tariffs.scan_tariffs` first match each feature/tariff start with a regex
and only `json.loads` the wanted ones (30–80× faster than streaming with `ijson`: 0.2 s vs 6–13 s
for a search). Both fall back to `ijson` when the layout changes; keep that fallback and its tests
(`tests/test_ndw_geojson.py`) if you touch them. `NdwFeed` caches parsed locations and reparses only when
the feed changed or a new id was requested.

## Home Assistant gotchas hit so far
- After a progress screen the next step receives the previous form's `user_input`: steps must
  only react to their own fields (`select`, `problem`).
- A subentry flow must not start with a progress screen: the frontend closes it as "created"
  without adding anything. The first step is a menu; the download is shown as its own progress
  step inside the search (`_after_fetch`).
- Adding to an existing place is an `async_update_subentry` + `async_abort("location_added")`;
  duplicate subentry `unique_id` aborts as `already_configured`.
- Subentry/option changes reload the entry via the update listener.
- Deleting a device removes its location from the place (`async_remove_config_entry_device`).
- Entity unique ids are `<location_id>_<what>`; changing them orphans users' entities.
- Old per-location entries (before the subentry structure) are not migrated.

## Develop and test
```
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python pytest-homeassistant-custom-component ijson==3.3.0
.venv/bin/python -m pytest          # all network is mocked (see tests/conftest.py)
```
`tests/conftest.py` holds small synthetic NDW/OCM payloads; the NDW test feed must keep the real
layout (`{"id":…,"type":"Feature","geometry":…`) for the fast parser path to be exercised
(`test_ndw_geojson.py` checks both paths). `scripts/check_*.py` verify against the live services
(`OCM_API_KEY=… python scripts/check_ocm.py`); `scripts/make_icon.py` regenerates `brand/`.

## Style
- Match the surrounding code; keep HA-free modules free of `homeassistant` imports.
- Translations: edit **both** `translations/en.json` and `nl.json` (steps, errors, entity names,
  `config_subentries.location`); icons in `icons.json`; names come from translation keys.
- Language of user-facing docs is English; the maintainer talks Dutch.

## Release
Bump `version` in `manifest.json`, push, then
`gh release create vX.Y.Z --repo willumpie82/ha-publicchargewatch --title vX.Y.Z --notes …`.
HACS also works without releases (it then offers the default branch).
