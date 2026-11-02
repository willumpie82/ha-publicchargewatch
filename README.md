# PublicChargeWatch (Home Assistant / HACS)

Public EV chargers as Home Assistant devices, from one of two sources chosen in the setup flow.

| | **NDW live API** (NL, recommended) | **NDW download** (NL) | **Open Charge Map** (worldwide) |
|---|---|---|---|
| Live free / in use | **yes**, per connector type | **yes** | **no**, only "believed to work" |
| Price | exact tariff incl. VAT | exact tariff incl. VAT | best-effort parse of free text |
| Account | none | none | free API key ([get one](https://openchargemap.org/site/develop/api)) |
| Data traffic | a few kB per refresh (+ ~4 MB tariffs once a day) | ~5 MB per refresh (up to ~1.4 GB/day at 5 min) | a few kB |
| Default refresh | 2 min (min 30 s) | 5 min (min 2 min) | 30 min (min 5 min) |
| Data quality | from the operators (CPO), minutes old | same | community, often months old |

The two NDW sources are the same data. The **live API** asks NDW only for the areas around your
chargers; the **download** fetches the whole national file and filters it itself (it was the first
version, and existing installations keep using it).

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
