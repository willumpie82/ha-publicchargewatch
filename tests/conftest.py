"""Shared fixtures: small synthetic NDW/OCM payloads and network mocks."""

import copy
import gzip
import json

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.ndw_laadpalen.const import FEED_URL, TARIFF_URL
from custom_components.ndw_laadpalen.geocode import PDOK_URL

# zone.home in the test hass instance
HOME_LAT, HOME_LON = 32.87336, -117.22743


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ndw_laadpalen."""


def _gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj).encode())


def feature(fid, address, lat, lon, groups, updated="2026-11-02T06:45:39Z"):
    return {
        "id": fid,
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "address": address,
            "operator_name": "Vattenfall InCharge",
            "last_updated": updated,
            "availabilities": groups,
        },
    }


def group(total, available, power=11040.0, tariffs=("T1",), connector="IEC_62196_T2"):
    return {
        "total": total,
        "available": available,
        "power_max": power,
        "power_type": "AC3",
        "tariff_ids": list(tariffs),
        "connector_type": connector,
        "connector_format": "SOCKET",
    }


NDW_FEED = {
    "features": [
        feature("NL-NUO-near", "Teststraat 1", HOME_LAT, HOME_LON, [group(2, 1)]),
        feature(
            "NL-NUO-two", "Teststraat 2", HOME_LAT + 0.0005, HOME_LON,
            [group(2, 2), group(1, 0, power=50000.0, tariffs=("T2",), connector="IEC_62196_T2_COMBO")],
        ),
        feature("NL-NUO-far", "Verweg 9", HOME_LAT + 1.0, HOME_LON, [group(2, 2)]),
    ]
}

NDW_TARIFFS = [
    {
        "id": "T1", "currency": "EUR",
        "tariff_alt_text": [{"language": "nl", "text": "Per kWh: 0.33 EUR"}],
        "elements": [{
            "price_components": [{"type": "ENERGY", "price": 0.2691, "vat": 21.0}],
            "restrictions": {"start_date": "2026-01-01", "end_date": None, "start_time": None},
        }],
        "last_updated": "2026-11-02T01:05:14Z",
    },
    {"id": "T2", "currency": "EUR", "elements": [{
        "price_components": [{"type": "ENERGY", "price": 0.5, "vat": 21.0}, {"type": "FLAT", "price": 1.0, "vat": 21.0}],
        "restrictions": {},
    }]},
]

OCM_POI = {
    "ID": 473176,
    "AddressInfo": {
        "AddressLine1": "Jacob Cnodestraat", "Postcode": "5223 HT", "Town": "Teststad",
        "Latitude": HOME_LAT, "Longitude": HOME_LON,
    },
    "OperatorInfo": {"Title": "Vattenfall InCharge"},
    "StatusTypeID": 50,
    "NumberOfPoints": 2,
    "UsageCost": "€0,34/kWh",
    "DateLastStatusUpdate": "2026-03-02T17:04:00Z",
    "Connections": [{
        "ID": 794706, "PowerKW": 11, "Quantity": 2, "StatusTypeID": 50,
        "ConnectionType": {"FormalName": "IEC 62196-2 Type 2"},
        "CurrentType": {"Title": "AC (Three-Phase)"},
    }],
}


NEAR_FREE = {"n": None}
CITY = {"name": "Teststad"}


def mock_city(aioclient_mock, city="Teststad"):
    """PDOK reverse geocoding: every point is in CITY['name'] (change it mid-test)."""
    CITY["name"] = city

    async def reverse(method, url, data):
        return AiohttpClientMockResponse(
            method, url, json={"response": {"docs": [{"woonplaatsnaam": CITY["name"]}]}}
        )

    aioclient_mock.get(PDOK_URL.replace("/free", "/reverse"), side_effect=reverse)


@pytest.fixture
def mock_ndw(aioclient_mock):
    headers = {"ETag": '"v1"'}
    NEAR_FREE["n"] = None

    async def feed(method, url, data):
        body = NDW_FEED
        if NEAR_FREE["n"] is not None:  # tests change the availability mid-test
            body = copy.deepcopy(NDW_FEED)
            body["features"][0]["properties"]["availabilities"][0]["available"] = NEAR_FREE["n"]
        return AiohttpClientMockResponse(method, url, response=_gz(body), headers=headers)

    aioclient_mock.get(FEED_URL, side_effect=feed)
    aioclient_mock.get(TARIFF_URL, content=_gz(NDW_TARIFFS), headers=headers)
    mock_city(aioclient_mock, "Teststad")
    return aioclient_mock


@pytest.fixture
def mock_ocm(aioclient_mock):
    from custom_components.ndw_laadpalen.ocm import OCM_URL

    aioclient_mock.get(OCM_URL, json=[OCM_POI])
    return aioclient_mock


@pytest.fixture(autouse=True)
def home_zone(hass):
    """The bare test instance has no zone.home; real installations always do."""
    hass.states.async_set(
        "zone.home", "0", {"latitude": HOME_LAT, "longitude": HOME_LON, "radius": 100}
    )
