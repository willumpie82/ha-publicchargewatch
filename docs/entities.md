[Setup](setup.md) · [Entities](entities.md) · [Dynamic charging location](dynamic.md) · [Examples](examples.md) · [Development](development.md)

# Entities

Created from the data the location actually has.
- Location device: *available charge points* (NDW) or *operational charge points* (OCM),
  *last status change* and *last update* (diagnostic; the latter is when we last fetched
  successfully, so it shows when updates are failing), *price per kWh* when known, and for NDW a *charge point available*
  binary sensor.
- A diagnostic *Refresh* button checks for new data right now (bypasses the shared 60 s window;
  the ETag still skips the download when NDW's file is unchanged).
- NDW live API: a diagnostic *Rate limited* binary sensor on the source's service device (hidden by default;
  enable it under the device) is on when NDW answered the last request with 429 (too many requests).
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
