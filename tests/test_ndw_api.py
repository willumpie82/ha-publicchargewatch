"""The NDW bounding-box API provider: same data as the bulk file, a few kB per refresh."""

import pytest

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.public_charge_watch.providers.ndw_shared import NdwTariffs
from custom_components.public_charge_watch.const import FEED_URL
from custom_components.public_charge_watch.providers.ndw_api import NdwApi, bounding_box
from custom_components.public_charge_watch.providers.base import ProviderError

from .conftest import HOME_LAT, HOME_LON
from .test_flows import _add_ndw_location, _entity, _update_feed


def test_bounding_box_is_capped_like_the_api():
    west, south, east, north = (float(v) for v in bounding_box(52.0, 5.0, 150).split(","))
    assert west < 5.0 < east and south < 52.0 < north
    with pytest.raises(ProviderError):
        bounding_box(52.0, 5.0, 80_000)  # far more than 1 square degree


async def test_api_source_stores_coordinates_and_never_downloads_the_big_file(hass, mock_ndw_api):
    result = await _add_ndw_location(hass, ["NL-NUO-near", "NL-NUO-two"], source="ndw_api")
    assert result["title"] == "NDW (live API)"
    entry = hass.config_entries.async_entries("public_charge_watch")[0]
    assert entry.unique_id == "ndw_api"
    (place,) = entry.subentries.values()
    assert place.data["coordinates"]["NL-NUO-near"] == [HOME_LAT, HOME_LON]

    assert _entity(hass, "sensor", "NL-NUO-near_available") is not None
    assert _entity(hass, "sensor", "NL-NUO-two_available") is not None
    assert not [m for m in mock_ndw_api.mock_calls if str(m[1]) == FEED_URL]  # no bulk download


async def test_api_refresh_sees_new_availability(hass, mock_ndw_api):
    await _add_ndw_location(hass, ["NL-NUO-near"], source="ndw_api")
    state = hass.states.get(_entity(hass, "sensor", "NL-NUO-near_available"))
    assert state.state == "1"
    await _update_feed(hass, mock_ndw_api, 0)
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-near_available")).state == "0"


async def test_nearby_locations_close_together_share_one_request(hass, mock_ndw_api):
    api = NdwApi(async_get_clientsession(hass), NdwTariffs(hass, async_get_clientsession(hass)))
    coords = {"NL-NUO-near": (HOME_LAT, HOME_LON), "NL-NUO-two": (HOME_LAT + 0.0005, HOME_LON)}
    found = await api.async_get_locations({"NL-NUO-near", "NL-NUO-two"}, coords=coords)
    assert set(found) == {"NL-NUO-near", "NL-NUO-two"}
    assert len(mock_ndw_api.api_calls) == 1  # 55 m apart: one small area covers both
    assert found["NL-NUO-near"]["price_per_kwh"] is not None


async def test_location_without_coordinates_is_left_out(hass, mock_ndw_api):
    api = NdwApi(async_get_clientsession(hass), NdwTariffs(hass, async_get_clientsession(hass)))
    assert await api.async_get_locations({"NL-NUO-near"}, coords={}) == {}
    assert mock_ndw_api.api_calls == []


async def test_dynamic_location_works_on_the_api_source(hass, mock_ndw_api):
    from .test_dynamic import _add_dynamic, _where

    _where(hass)
    await _add_ndw_location(hass, ["NL-NUO-near"], source="ndw_api")
    entry, dynamic = await _add_dynamic(hass)
    sensor = _entity(hass, "sensor", f"{dynamic.subentry_id}_nearest")
    assert hass.states.get(sensor).state == "Teststraat 1"
    assert not [m for m in mock_ndw_api.mock_calls if str(m[1]) == FEED_URL]


async def test_large_areas_are_read_page_by_page(hass, mock_ndw_api):
    mock_ndw_api.api_page_size = 1  # every location its own page
    api = NdwApi(async_get_clientsession(hass), NdwTariffs(hass, async_get_clientsession(hass)))
    found = await api.async_nearby(HOME_LAT, HOME_LON, 200)
    assert [loc["id"] for loc in found] == ["NL-NUO-near", "NL-NUO-two"]
    assert len(mock_ndw_api.api_calls) == 2  # two pages


async def test_incomplete_answer_is_an_error(hass, mock_ndw_api):
    from custom_components.public_charge_watch.const import DOTNL_URL
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    async def partial(method, url, data):  # claims 5 locations, delivers none, no next page
        return AiohttpClientMockResponse(
            method, url, response=b'{"features": []}', headers={"x-total-count": "5"}
        )

    mock_ndw_api.clear_requests()
    mock_ndw_api.get(DOTNL_URL, side_effect=partial)
    api = NdwApi(async_get_clientsession(hass), NdwTariffs(hass, async_get_clientsession(hass)))
    with pytest.raises(ProviderError):
        await api.async_nearby(HOME_LAT, HOME_LON, 200)


async def test_one_rate_limited_request_is_retried(hass, mock_ndw_api, monkeypatch):
    from custom_components.public_charge_watch.providers import ndw_api as module

    monkeypatch.setattr(module, "_RETRY_DELAY", 0)
    from custom_components.public_charge_watch.const import DOTNL_URL
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
    import json

    calls = {"n": 0}

    async def flaky(method, url, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return AiohttpClientMockResponse(method, url, status=429)
        return AiohttpClientMockResponse(
            method, url, response=json.dumps({"features": []}).encode(), headers={"x-total-count": "0"}
        )

    mock_ndw_api.clear_requests()
    mock_ndw_api.get(DOTNL_URL, side_effect=flaky)
    api = NdwApi(async_get_clientsession(hass), NdwTariffs(hass, async_get_clientsession(hass)))
    assert await api.async_nearby(HOME_LAT, HOME_LON, 200) == []
    assert calls["n"] == 2  # 429, then the retry


async def test_rate_limit_keeps_the_last_data_for_a_while(hass, mock_ndw_api, monkeypatch):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.public_charge_watch.const import DOTNL_URL
    from custom_components.public_charge_watch.providers import ndw_api as module
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    monkeypatch.setattr(module, "_RETRY_DELAY", 0)
    await _add_ndw_location(hass, ["NL-NUO-near"], source="ndw_api")
    entry = hass.config_entries.async_entries("public_charge_watch")[0]
    coordinator = entry.runtime_data
    sensor = _entity(hass, "sensor", "NL-NUO-near_available")
    assert hass.states.get(sensor).state == "1"

    async def limited(method, url, data):
        return AiohttpClientMockResponse(method, url, status=429)

    mock_ndw_api.clear_requests()
    mock_ndw_api.get(DOTNL_URL, side_effect=limited)

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "1"  # still the last known value, not unavailable

    coordinator.last_success = dt_util.utcnow() - 4 * coordinator.update_interval  # too old now
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "unavailable"
