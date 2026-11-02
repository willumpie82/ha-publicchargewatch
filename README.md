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

## Documentation
- **[Setup](docs/setup.md)**: sources, places and charging locations, and the setup flow.
- **[Entities](docs/entities.md)**: what is created per charging location, and how updates behave.
- **[Dynamic charging location](docs/dynamic.md)**: follow a person or car and always see the nearest suitable, free charger, with a location tracker to navigate to (NDW).
- **[Examples](docs/examples.md)**: map card, markdown card, automations with the events, template sensor.
- **[Development](docs/development.md)**: tests and the checks against the live services.
