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
| `parser.py`, `ndw_tariff.py`, `tariff.py`, `ocm.py`, `geocode.py` | Home-Assistant-free logic (also used by `scripts/`) |
| `api.py` | `NdwFeed`: shared NDW download (raw feed kept in memory, ETag, lock) + daily tariff download persisted in a `Store` |
| `coordinator.py` | polling, `iter_locations(entry)`, force refresh, `last_success` |
| `config_flow.py` | `_LocationSearch` mixin (search steps) shared by `NdwConfigFlow` and `LocationSubentryFlow`; options flow = update interval |
| `sensor.py` `binary_sensor.py` `button.py` `entity.py` | entities, created from the data a location actually has |
| `dynamic.py`, `DynamicCoordinator` | dynamic charging location (subentry type `dynamic`, NDW only): `pick()` ranks nearby chargers by plug / power / free; the coordinator follows a tracked entity (state listener in `__init__.py`, recompute after ~150 m); sensor, `device_tracker` and event entities live on its own device (`dynamic_<subentry_id>`) |

## Data sources (the non-obvious parts)
- **NDW** (NL only, live availability per connector group, exact tariffs): GeoJSON
  `charging_point_locations.geojson.gz` (5 MB gz / 41 MB, ~81k features, no city/postcode) and
  OCPI `charging_point_tariffs_ocpi.json.gz` (4 MB, ~105k tariffs; price is excl. VAT, `vat` in %,
  the integration shows incl. VAT). There is no regional filter, so every refresh downloads the
  whole feed (~1.4 GB/day at 5 min): all entries share one download (`max_age` = 80% of the
  interval), HTTP 304 when unchanged. Tariffs: at most once a day.
- **Open Charge Map**: API key (header `X-API-Key`), no live occupancy (status 50 = "last editor
  said it works", not "free"). Prices are free text; `tariff.py` parses them conservatively
  ("0.00 jaarabonnement" is an import placeholder = unknown, never free).
- **PDOK Locatieserver** (no key): postcode → point (`free`, must verify the returned postcode
  because matching is fuzzy) and point → town (`reverse`, NDW has no city).
- Not everything exists: private/semi-public chargers (e.g. company sites) are in neither NDW nor
  OCM. That is a data limit, not a bug.

## Performance rule: never build objects for the whole feed
`parser.scan` and `ndw_tariff.scan_tariffs` first match each feature/tariff start with a regex
and only `json.loads` the wanted ones (30–80× faster than streaming with `ijson`: 0.2 s vs 6–13 s
for a search). Both fall back to `ijson` when the layout changes; keep that fallback and its tests
(`tests/test_parser.py`) if you touch them. `NdwFeed` caches parsed locations and reparses only when
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
(`test_parser.py` checks both paths). `scripts/check_*.py` verify against the live services
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
