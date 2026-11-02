"""Test the Open Charge Map data path against the live API (no Home Assistant needed).

Usage:
    pip install ijson==3.3.0 aiohttp
    OCM_API_KEY=<your key> python scripts/check_ocm.py [LAT LON [RADIUS_M]]

Get a free key at https://openchargemap.org/site/develop/api
Defaults to Jacob Cnodestraat, 's-Hertogenbosch.
"""

import asyncio
import importlib
import os
from pathlib import Path
import sys
import types

import aiohttp

# Load the integration's modules without executing its __init__ (needs Home Assistant).
pkg_dir = Path(__file__).parent.parent / "custom_components/ndw_laadpalen"
pkg = types.ModuleType("ndw_pkg")
pkg.__path__ = [str(pkg_dir)]
sys.modules["ndw_pkg"] = pkg
ocm = importlib.import_module("ndw_pkg.ocm")

lat, lon, radius = 51.69427, 5.27172, 150
if len(sys.argv) >= 3:
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
if len(sys.argv) >= 4:
    radius = float(sys.argv[3])

# 1. offline: parsing a minimal OCM POI
poi = {
    "ID": 1,
    "AddressInfo": {"Title": "T", "AddressLine1": "Straat", "Town": "Stad",
                    "Postcode": "1234 AB", "Latitude": 52.0, "Longitude": 5.0},
    "OperatorInfo": {"Title": "Op"},
    "StatusTypeID": 50,
    "NumberOfPoints": 2,
    "Connections": [
        {"ID": 9, "PowerKW": 11, "Quantity": 2, "StatusTypeID": 50,
         "ConnectionType": {"FormalName": "IEC 62196-2 Type 2"}},
        {"ID": 10, "StatusTypeID": 100},
        {"ID": 11, "StatusType": {"IsOperational": False}},
        {},
    ],
}
loc = ocm.parse_poi(poi)
assert loc["id"] == "ocm-1" and loc["points"] == 2 and loc["operator"] == "Op"
assert [e["status"] for e in loc["evses"]] == ["OPERATIONAL", "OUTOFORDER", "OUTOFORDER", "OPERATIONAL"]
assert loc["evses"][0]["connectors"][0]["max_power_w"] == 11000
assert loc["evses"][0]["connectors"][0]["quantity"] == 2
assert ocm.parse_poi({"ID": 2, "AddressInfo": {}}) is None
print("offline parse: OK")


async def main() -> None:
    key = os.environ.get("OCM_API_KEY")
    if not key:
        print("OCM_API_KEY not set: skipping live checks")
        return
    async with aiohttp.ClientSession() as session:
        client = ocm.OcmClient(session, key)
        await client.async_validate()
        print("validate: OK")

        try:
            await ocm.OcmClient(session, "invalid-key").async_validate()
        except ocm.OcmAuthError:
            print("invalid key rejected: OK")
        else:
            raise AssertionError("an invalid key was accepted")

        near = await client.async_nearby(lat, lon, radius)
        print(f"nearby: {len(near)} locations within {radius:.0f} m")
        assert near, "nothing found; wrong coordinates?"
        for l in near:
            print(f"  {l['id']} {l['address']} {l['postal_code']} ({l['operator']}) "
                  f"{l['distance']} m, points={l['points']}")
            for e in l["evses"]:
                print("    ", e["evse_id"], e["status"], e["connectors"])

        for l in near:
            print(f"  price: {l['price_text']!r} -> {l['price_per_kwh']} ({l['price_basis']}), "
                  f"updated {l['last_updated']}")

        again = await client.async_get_locations({near[0]["id"]})
        assert again[near[0]["id"]]["evses"] == near[0]["evses"]
        print("lookup by id: OK")


asyncio.run(main())
print("OK")
