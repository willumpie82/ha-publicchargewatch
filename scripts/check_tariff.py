"""Offline test of the OCM UsageCost parser, using real strings seen in Dutch OCM data."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "tariff",
    Path(__file__).parent.parent / "custom_components/public_charge_watch/providers/ocm_tariff.py",
)
tariff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tariff)

# (text, expected price, expected basis)
CASES = [
    ("0.00 jaarabonnement", None, "unknown"),  # import placeholder, NOT free
    ("0.00 afhankelijk van pas", None, "unknown"),
    ("0.00 gratis", 0.0, "free"),
    ("Free", 0.0, "free"),
    ("€0.25/kWh; andere tarieven voor oudere auto's", 0.25, "per_kwh"),
    ("0.3388", 0.3388, "assumed_per_kwh"),
    ("€0.34/kWh", 0.34, "per_kwh"),
    ("0.00 per laadbeurt", None, "unknown"),
    ("0.34", 0.34, "assumed_per_kwh"),
    ("€0.59/kWh incl. VAT", 0.59, "per_kwh"),
    ("0.00", None, "unknown"),
    ("0,33€", 0.33, "assumed_per_kwh"),
    ("0,336", 0.336, "assumed_per_kwh"),
    ("0.30", 0.30, "assumed_per_kwh"),
    ("0.00 per kWh", None, "unknown"),
    ("0.34 / kWh", 0.34, "per_kwh"),
    ("€0,37/kWh", 0.37, "per_kwh"),
    ("40€", None, "unknown"),
    ("6.00 per laadbeurt", None, "unknown"),
    ("€0,36/kWh", 0.36, "per_kwh"),
    ("€0.59/kWh", 0.59, "per_kwh"),
    ("0,00", None, "unknown"),
    ("4€/10minutes                 Pre-paid card or cash", None, "unknown"),
    ("", None, "unknown"),
    (None, None, "unknown"),
    ("Paid", None, "unknown"),
    ("Only during opening hours of the Ikea store", None, "unknown"),
    ("All major Dutch charge cards (Elaad, EV-Box, Nuon, Essent etc)", None, "unknown"),
    ("0.00 onbekend", None, "unknown"),
    ("-", None, "unknown"),
    ("0.59 per kWH", 0.59, "per_kwh"),
    ("0,33 €/kWh", 0.33, "per_kwh"),
    ("EUR 0.45 per kWh", 0.45, "per_kwh"),
]

failed = 0
for text, price, basis in CASES:
    got = tariff.parse_usage_cost(text)
    if got != {"price_per_kwh": price, "basis": basis}:
        failed += 1
        print(f"FAIL {text!r}: expected ({price}, {basis}), got {got}")
assert not failed, f"{failed} case(s) failed"
print(f"{len(CASES)} cases OK")
