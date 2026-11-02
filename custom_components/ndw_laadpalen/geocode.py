"""Dutch postcode -> coordinates via the PDOK Locatieserver (free, no key, no Home Assistant)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
_POSTCODE = re.compile(r"^\s*(\d{4})\s*([A-Za-z]{2})\s*$")
_POINT = re.compile(r"POINT\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")


def normalize_postcode(text: str) -> str | None:
    """'5223 hs' -> '5223HS', or None when it is not a Dutch postcode."""
    if match := _POSTCODE.match(text or ""):
        return f"{match.group(1)}{match.group(2).upper()}"
    return None


async def _lookup(
    session: aiohttp.ClientSession, query: str, doc_type: str, postcode: str
) -> tuple[float, float] | None:
    import aiohttp

    async with session.get(
        PDOK_URL,
        params={
            "q": query,
            "fq": f"type:{doc_type}",
            "rows": 1,
            "fl": "centroide_ll,postcode",
        },
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        resp.raise_for_status()
        docs = (await resp.json(content_type=None))["response"]["docs"]
    # PDOK matches fuzzily (an unknown house number can return an address elsewhere in the
    # country), so only accept a result in the postcode that was asked for.
    if (
        docs
        and (docs[0].get("postcode") or "").upper() == postcode
        and (match := _POINT.search(docs[0].get("centroide_ll", "")))
    ):
        return float(match.group(2)), float(match.group(1))  # POINT(lon lat)
    return None


async def async_geocode_postcode(
    session: aiohttp.ClientSession, postcode: str, house_number: str | None = None
) -> tuple[float, float] | None:
    """(latitude, longitude) of a postcode, exact to the address when a house number is given."""
    code = normalize_postcode(postcode)
    if code is None:
        return None
    if house_number and house_number.strip():
        if point := await _lookup(
            session, f"{code} {house_number.strip()}", "adres", code
        ):
            return point
    return await _lookup(session, code, "postcode", code)
