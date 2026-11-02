"""Live test of the PDOK postcode geocoder (no key, no Home Assistant needed).

Usage: pip install aiohttp && python scripts/check_geocode.py
"""

import asyncio
import importlib
from pathlib import Path
import sys
import types

import aiohttp

pkg = types.ModuleType("ndw_pkg")
pkg.__path__ = [str(Path(__file__).parent.parent / "custom_components/ndw_laadpalen")]
sys.modules["ndw_pkg"] = pkg
geo = importlib.import_module("ndw_pkg.geocode")

assert geo.normalize_postcode("5223 hs") == "5223HS"
assert geo.normalize_postcode(" 5223HS ") == "5223HS"
assert geo.normalize_postcode("5223") is None
assert geo.normalize_postcode("ABCD12") is None
assert geo.normalize_postcode("") is None


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        postcode_level = await geo.async_geocode_postcode(s, "5223 HS")
        address_level = await geo.async_geocode_postcode(s, "5223HS", "23")
        unknown_number = await geo.async_geocode_postcode(s, "5223HS", "99999")
        nowhere = await geo.async_geocode_postcode(s, "0000AA")
        print("postcode:", postcode_level, "| with number:", address_level,
              "| unknown number:", unknown_number, "| bad postcode:", nowhere)
        assert postcode_level and abs(postcode_level[0] - 51.694) < 0.01
        assert address_level and abs(address_level[1] - 5.2718) < 0.001
        assert unknown_number == postcode_level  # falls back to the postcode
        assert nowhere is None


asyncio.run(main())
print("OK")
