"""Test the NDW data path against the live feeds (no Home Assistant needed).

Usage:
    pip install ijson==3.3.0
    python scripts/check_ndw.py [LAT LON [RADIUS_M]]

Defaults to Jacob Cnodestraat 23, 's-Hertogenbosch.
Checks: GeoJSON download, ETag/304, nearby search, lookup by id, tariff download + lookup.
"""

from datetime import date
import importlib
import sys
import time
from pathlib import Path
import types
import urllib.error
import urllib.request

BASE = "https://opendata.ndw.nu/"
FEED_URL = BASE + "charging_point_locations.geojson.gz"
TARIFF_URL = BASE + "charging_point_tariffs_ocpi.json.gz"

pkg_dir = Path(__file__).parent.parent / "custom_components/ndw_laadpalen"
pkg = types.ModuleType("ndw_pkg")
pkg.__path__ = [str(pkg_dir)]
sys.modules["ndw_pkg"] = pkg
parser = importlib.import_module("ndw_pkg.parser")
ndw_tariff = importlib.import_module("ndw_pkg.ndw_tariff")

lat, lon, radius = 51.69427, 5.27172, 150
if len(sys.argv) >= 3:
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
if len(sys.argv) >= 4:
    radius = float(sys.argv[3])


def download(url):
    t = time.time()
    with urllib.request.urlopen(url, timeout=180) as resp:
        raw, etag = resp.read(), resp.headers.get("ETag")
    print(f"download {url.rsplit('/', 1)[1]}: {len(raw) / 1e6:.1f} MB in {time.time() - t:.1f}s, ETag={etag}")
    assert etag, "feed no longer sends an ETag; api.py relies on it"
    return raw, etag


def assert_304(url, etag):
    req = urllib.request.Request(url, headers={"If-None-Match": etag})
    try:
        urllib.request.urlopen(req, timeout=60)
        print("  conditional request: 200 (feed changed in between)")
    except urllib.error.HTTPError as err:
        assert err.code == 304, err
        print("  conditional request: 304 OK")


# 1. locations
raw, etag = download(FEED_URL)
assert_304(FEED_URL, etag)

t = time.time()
near = parser.find_nearby(raw, lat, lon, radius)
print(f"find_nearby: {len(near)} locations within {radius:.0f} m in {time.time() - t:.1f}s")
assert near, "nothing found; wrong coordinates or empty feed"
for loc in near:
    print(f"  {loc['id']} {loc['address']} ({loc['operator']}) {loc['distance']} m, points={loc['points']}")
    for g in loc["groups"]:
        print(f"     {g['key']}: {g['available']}/{g['total']} free, {parser.group_label(g)}, tariffs={g['tariff_ids']}")

target = near[0]
t = time.time()
by_id = parser.find_by_ids(raw, {target["id"]})
print(f"find_by_ids: {list(by_id)} in {time.time() - t:.1f}s")
assert by_id[target["id"]]["groups"] == target["groups"]

# 2. tariffs
tariff_ids = {i for g in target["groups"] for i in g["tariff_ids"]}
if tariff_ids:
    raw_t, etag_t = download(TARIFF_URL)
    assert_304(TARIFF_URL, etag_t)
    t = time.time()
    tariffs = ndw_tariff.scan_tariffs(raw_t, tariff_ids, date.today())
    print(f"scan_tariffs: {len(tariffs)}/{len(tariff_ids)} found in {time.time() - t:.1f}s")
    for tid, tariff in tariffs.items():
        print(" ", tid, tariff)
    assert tariffs, "tariff ids from the location feed not in the tariff file"
else:
    print("no tariff ids on this location")
print("OK")
