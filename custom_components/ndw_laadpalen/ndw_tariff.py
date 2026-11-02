"""Reduce NDW OCPI tariffs to what Home Assistant shows (no Home Assistant imports)."""

from __future__ import annotations

from datetime import date
import gzip
import io
from typing import Any

import ijson

# restriction fields that make an element conditional (time of day, size, ...)
_CONDITIONAL = (
    "start_time",
    "end_time",
    "day_of_week",
    "min_kwh",
    "max_kwh",
    "min_current",
    "max_current",
    "min_power",
    "max_power",
    "min_duration",
    "max_duration",
    "reservation",
)


def _active_today(restrictions: dict[str, Any], today: date) -> bool:
    start, end = restrictions.get("start_date"), restrictions.get("end_date")
    if start and date.fromisoformat(start) > today:
        return False
    return not (end and date.fromisoformat(end) < today)


def _with_vat(component: dict[str, Any]) -> float:
    """OCPI prices are excluding VAT; 'vat' is a percentage (may be missing)."""
    price = component["price"]
    vat = component.get("vat")
    return price * (1 + vat / 100) if vat else price


def simplify_tariff(tariff: dict[str, Any], today: date) -> dict[str, Any]:
    """Standard (unconditional, currently valid) prices incl. VAT, plus a 'complex' flag.

    'complex' is true when conditional elements exist (e.g. day/night rates) that are not
    reflected in the headline price.
    """
    prices: dict[str, dict[str, Any]] = {}
    is_complex = False
    for element in tariff.get("elements") or []:
        restrictions = element.get("restrictions") or {}
        if not _active_today(restrictions, today):
            continue
        if any(restrictions.get(field) for field in _CONDITIONAL):
            is_complex = True
            continue
        for comp in element.get("price_components") or []:
            prices.setdefault(comp.get("type"), comp)

    def rounded(kind: str) -> float | None:
        return round(_with_vat(prices[kind]), 4) if kind in prices else None

    texts = {t.get("language"): t.get("text") for t in tariff.get("tariff_alt_text") or []}
    return {
        "energy_price": rounded("ENERGY"),  # per kWh, incl. VAT
        "start_fee": rounded("FLAT"),
        "time_price_per_hour": rounded("TIME"),
        "parking_price_per_hour": rounded("PARKING_TIME"),
        "vat": (prices.get("ENERGY") or {}).get("vat"),
        "currency": tariff.get("currency"),
        "complex": is_complex,
        "text": texts.get("nl") or texts.get("en") or next(iter(texts.values()), None),
        "last_updated": tariff.get("last_updated"),
    }


def scan_tariffs(raw: bytes, ids: set[str], today: date) -> dict[str, dict[str, Any]]:
    """Stream the gzipped tariff file (~105k tariffs) and keep only the requested ids."""
    found: dict[str, dict[str, Any]] = {}
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        for tariff in ijson.items(stream, "item", use_float=True):
            if tariff.get("id") in ids:
                found[tariff["id"]] = simplify_tariff(tariff, today)
    return found
