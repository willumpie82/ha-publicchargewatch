# PublicChargeWatch (Home Assistant / HACS)

Public EV chargers as Home Assistant devices, from one of two sources chosen in the setup flow.

| | **NDW** (Netherlands only) | **Open Charge Map** (worldwide) |
|---|---|---|
| Live free / in use | **yes**, per connector type | **no**, only "believed to work" |
| Price | exact tariff incl. VAT (e.g. 0.3256 €/kWh) | best-effort parse of free text, ~5% of NL locations |
| Account | none | free API key ([get one](https://openchargemap.org/site/develop/api)) |
| Data traffic | ~5 MB per refresh (up to ~1.4 GB/day at 5 min) + ~4 MB tariffs once a day | a few kB |
| Default refresh | 5 min (min 2 min) | 30 min (min 5 min) |
| Data quality | from the operators (CPO), minutes old | community, often months old |

The old oplaadpalen.nl API is gone (`410 Gone`).

## Install
HACS → Custom repositories → add this repo (type: Integration) → install → restart HA.
Settings → Devices & services → Add integration → "PublicChargeWatch".

## Structure
**Source** (NDW / Open Charge Map, one entry each) → **place** (a town, one subentry) →
**charging location** (one device, named by its address). "Add charging location" (⋮ on the
entry) adds the charger to the place it is in, or creates that place. The town comes from OCM, or
for NDW from the PDOK geocoder (the NDW feed has no town). Delete a charger's device to remove
it from its place. The update interval is set per source (⋮ → Configure).

## Setup flow
1. **Source**: NDW or OCM, with the pros and cons. OCM asks for its API key and checks it.
2. **NDW only, first time**: a confirmation explains the download (what, why, how often) with
   "Continue" or "Back". Adding locations later skips it.
3. **Where to search**: a zone (default Home), a Dutch postcode (+ optional house number, via the
   free PDOK geocoder) or a point on the map; radius 50–500 m (default 200).
4. **Pick a charger** from a list with distance, availability, power and price.
5. **Refresh interval**, with the data-traffic note for NDW.
Further locations repeat steps 3–4 only (already added ones are hidden).

## Entities (created from the data the location actually has)
- Location device: *available charge points* (NDW) or *operational charge points* (OCM),
  *last status change* and *last update* (diagnostic; the latter is when we last fetched
  successfully, so it shows when updates are failing), *price per kWh* when known, and for NDW a *charge point available*
  binary sensor.
- A diagnostic *Refresh* button checks for new data right now (bypasses the shared 60 s window;
  the ETag still skips the download when NDW's file is unchanged).
- NDW: an *Availability* event entity per location fires `became_available` (first free charge
  point) and `became_occupied` (last one taken), with `free`, `total` and `address` as attributes.
  Optionally gate it: on the place (⋮ → Reconfigure) pick a binary sensor or input boolean per
  location; events then only fire while that entity is on (e.g. "I need to charge").
- NDW with several connector groups (e.g. Type 2 + CCS): one *available* sensor per group.
- OCM: a status sensor per connection group (`Quantity` = identical sockets, as attribute).
- Price attributes: source text, basis (`tariff`, `per_kwh`, `assumed_per_kwh`, `free`, `unknown`),
  VAT, start fee, time/parking price, and whether conditional (day/night) rates exist.

## How it behaves
- NDW feeds are streamed with `ijson` (never loaded whole), shared by all NDW locations
  (one download serves every location within ~80% of the update interval, even when their
  timers are offset; HTTP 304 when unchanged; the raw feed stays in memory, ~5 MB).
- Tariffs are fetched at most once a day and stored, so a restart does not re-download them.
  If the tariff download fails the last known price stays and availability is unaffected.
- OCM prices: "0.00 jaarabonnement" (84% of NL locations) is an import placeholder meaning
  *unknown*, not free, and is never shown as 0.

## Development
```
# integration tests (Home Assistant, mocked network)
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python pytest-homeassistant-custom-component ijson==3.3.0
.venv/bin/python -m pytest

# checks against the live services (no Home Assistant needed)
pip install ijson==3.3.0 aiohttp
python scripts/check_ndw.py              # NDW locations + tariffs
python scripts/check_geocode.py          # PDOK postcode lookup
python scripts/check_tariff.py           # OCM price text parser, offline
OCM_API_KEY=... python scripts/check_ocm.py
```
