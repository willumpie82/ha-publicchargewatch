"""Best-effort parser for Open Charge Map's free-text UsageCost (no Home Assistant imports).

Observed in Dutch OCM data (1769 locations): 84% say "0.00 jaarabonnement" or
"0.00 afhankelijk van pas". That is an import artefact meaning "price unknown", NOT free,
so a zero only counts as free when the text says so explicitly.
"""

from __future__ import annotations

import re
from typing import Any

_NUM = r"(\d+(?:[.,]\d+)?)"
_CUR = r"(?:€|eur(?:o)?)"

# explicit per-kWh price: "€0.25/kWh", "0,34 / kWh", "0.59 per kWH", "0,33 €/kWh"
_PER_KWH = re.compile(
    rf"{_CUR}?\s*{_NUM}\s*{_CUR}?\s*(?:/|per|p\.)\s*kwh", re.IGNORECASE
)
_FREE = re.compile(r"\b(gratis|free)\b", re.IGNORECASE)
# a price in some other unit: not usable as a per-kWh price
_OTHER_UNIT = re.compile(
    r"(laadbeurt|sessie|session|minut|minuut|min\b|uur|hour|\bh\b|dag|day|abonnement|"
    r"subscription|pas\b|card|kaart)",
    re.IGNORECASE,
)
# the whole text is just a number, optionally with a euro sign: "0.34", "0,33€"
_BARE = re.compile(rf"^\s*{_CUR}?\s*{_NUM}\s*{_CUR}?\s*$", re.IGNORECASE)

# a bare number is only believed to be €/kWh in a plausible range
_PLAUSIBLE = (0.05, 2.0)


def _to_float(text: str) -> float:
    return float(text.replace(",", "."))


def parse_usage_cost(text: str | None) -> dict[str, Any]:
    """Return {'price_per_kwh': float|None, 'basis': str}.

    basis: 'per_kwh' (explicit), 'free', 'assumed_per_kwh' (bare number),
    or 'unknown' (price_per_kwh is None).
    """
    if not text or not text.strip():
        return {"price_per_kwh": None, "basis": "unknown"}

    if match := _PER_KWH.search(text):
        value = _to_float(match.group(1))
        if value > 0:
            return {"price_per_kwh": value, "basis": "per_kwh"}
        # "0.00 per kWh" without a word saying free is an import placeholder
        if _FREE.search(text):
            return {"price_per_kwh": 0.0, "basis": "free"}
        return {"price_per_kwh": None, "basis": "unknown"}

    if _FREE.search(text):
        return {"price_per_kwh": 0.0, "basis": "free"}

    if _OTHER_UNIT.search(text):
        return {"price_per_kwh": None, "basis": "unknown"}

    if match := _BARE.match(text):
        value = _to_float(match.group(1))
        if _PLAUSIBLE[0] <= value <= _PLAUSIBLE[1]:
            return {"price_per_kwh": value, "basis": "assumed_per_kwh"}

    return {"price_per_kwh": None, "basis": "unknown"}
