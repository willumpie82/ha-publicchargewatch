[Setup](setup.md) · [Entities](entities.md) · [Dynamic charging location](dynamic.md) · [Examples](examples.md) · [Development](development.md)

# Setup

## Structure
**Source** (NDW / Open Charge Map, one entry each) → **place** (a town, one subentry) →
**charging location** (one device, named by its address). "Add charging location" (⋮ on the
entry) adds the charger to the place it is in, or creates that place. The town comes from OCM, or
for NDW from the PDOK geocoder (the NDW feed has no town). Delete a charger's device to remove
it from its place. The update interval is set per source (⋮ → Configure).
For the NDW live API there is also a **lookup radius** (default 150 m): around each known charger
NDW is asked for an area of that radius, and chargers that lie within it of each other share one
request. A larger radius means fewer API calls but more data per call; in a dense city a small
radius is enough, in a sparse area a larger one saves requests.

## Setup flow
1. **Source**: NDW or OCM, with the pros and cons. OCM asks for its API key and checks it.
2. **NDW only, first time**: a confirmation explains the download (what, why, how often) with
   "Continue" or "Back". Adding locations later skips it.
3. **Where to search**: a zone (default Home), a Dutch postcode (+ optional house number, via the
   free PDOK geocoder) or a point on the map; radius 50–500 m (default 200).
4. **Pick a charger** from a list with distance, availability, power and price.
5. **Refresh interval**, with the data-traffic note for NDW.
Further locations repeat steps 3–4 only (already added ones are hidden).
