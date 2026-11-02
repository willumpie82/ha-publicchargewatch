[Setup](setup.md) · [Entities](entities.md) · [Dynamic charging location](dynamic.md) · [Examples](examples.md) · [Development](development.md)

# Dynamic charging location (NDW)
"Add dynamic charging location" (⋮ on the NDW entry) follows a person, device tracker or zone and
always shows the nearest charger that has a suitable plug and a free charge point. Choose the
entity to follow, the search radius (up to 10 km), the plug types (default Type 2 + CCS), a minimum
power and whether chargers without a free charge point are skipped. It creates:
- a **sensor** with the address of the best charger and, as attributes, distance, free/total
  charge points, power, price per kWh, operator, plug types, a `navigate_url` (starts navigation on
  a phone) and the top 3 as `results`;
- separate **sensors** for *distance* (m), *plug type*, *free charge points* (attribute `free_of_total`,
  e.g. `1/3`), *power* (kW) and *last status change*, plus a diagnostic *Refresh* button;
- a **location tracker** at that charger, usable as a navigation target (map card, zones, templates).
  It is `unknown` when nothing qualifies, so it never points at a stale charger;
- an **event** `nearest_changed`, fired when a different charger with a free charge point becomes
  the best one (same attributes as the sensor), to build "tell me where I can charge" automations.

It searches the NDW data the entry already holds, so following a moving car costs no extra download;
it recomputes on every refresh and when the followed entity has moved about 150 m.

See [Examples](examples.md) for a map card, a markdown card and a "tell me where I can charge" automation.
