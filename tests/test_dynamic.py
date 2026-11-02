"""Dynamic charging location: the charger nearest to a tracked entity."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.public_charge_watch.const import DOMAIN
from custom_components.public_charge_watch.dynamic import pick

from .conftest import HOME_LAT, HOME_LON
from .test_flows import _add_ndw_location, _configure_sub, _entity, _update_feed

T2, CCS = "IEC_62196_T2", "IEC_62196_T2_COMBO"


def _loc(fid, distance, groups, price=None):
    return {
        "id": fid, "address": fid, "operator": "X", "latitude": 1.0, "longitude": 2.0,
        "distance": distance, "groups": groups, "price_per_kwh": price, "prices_by_group": {},
    }


def _g(connector, total, available, power=11000):
    return {"key": f"{connector}_{power}", "connector_type": connector, "power_w": power,
            "total": total, "available": available}


def test_pick_filters_on_plug_and_power_and_puts_free_first():
    near_full = _loc("near-full", 50, [_g(T2, 2, 0)])
    mid_free = _loc("mid-free", 300, [_g(T2, 2, 1), _g(CCS, 1, 0, 50000)])
    far_ccs = _loc("far-ccs", 900, [_g(CCS, 1, 1, 50000)])
    locs = [near_full, mid_free, far_ccs]

    assert [r["id"] for r in pick(locs, [T2], 0, False, 5)] == ["mid-free", "near-full"]
    assert [r["id"] for r in pick(locs, [T2], 0, True, 5)] == ["mid-free"]
    assert [r["id"] for r in pick(locs, [CCS], 0, True, 5)] == ["far-ccs"]
    assert [r["id"] for r in pick(locs, [], 20, True, 5)] == ["far-ccs"]  # >= 20 kW
    best = pick(locs, [T2], 0, True, 5)[0]
    assert (best["free"], best["total"], best["connectors"]) == (1, 2, ["Type 2"])
    assert best["navigate_url"].endswith("destination=1.000000,2.000000")


async def _add_dynamic(hass, **overrides):
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "dynamic"), context={"source": SOURCE_USER}
    )
    assert flow["type"] is FlowResultType.FORM
    flow = await _configure_sub(
        hass,
        flow,
        {
            "name": "Car",
            "tracked_entity": "person.me",
            "radius": 1000,
            "connector_types": [T2],
            "min_power_kw": 0,
            "only_available": True,
            **overrides,
        },
    )
    assert flow["type"] is FlowResultType.CREATE_ENTRY, flow
    await hass.async_block_till_done()
    (dynamic,) = [s for s in entry.subentries.values() if s.subentry_type == "dynamic"]
    return entry, dynamic


def _where(hass, lat=HOME_LAT, lon=HOME_LON):
    hass.states.async_set("person.me", "home", {"latitude": lat, "longitude": lon})


async def test_dynamic_location_tracks_the_nearest_free_charger(hass, mock_ndw):
    _where(hass)
    await _add_ndw_location(hass, "NL-NUO-near")
    entry, dynamic = await _add_dynamic(hass)

    sensor = _entity(hass, "sensor", f"{dynamic.subentry_id}_nearest")
    tracker = _entity(hass, "device_tracker", f"{dynamic.subentry_id}_nearest_tracker")
    state = hass.states.get(sensor)
    assert state.state == "Teststraat 1"
    assert state.attributes["free"] == 1 and state.attributes["total"] == 2
    assert state.attributes["price_per_kwh"] is not None
    assert [r["id"] for r in state.attributes["results"]] == ["NL-NUO-near", "NL-NUO-two"]
    track = hass.states.get(tracker)
    assert track.attributes["latitude"] == HOME_LAT
    assert track.attributes["navigate_url"].startswith("https://www.google.com/maps/dir/")


async def test_dynamic_location_follows_the_tracked_entity_and_never_shows_stale_data(
    hass, mock_ndw
):
    _where(hass)
    await _add_ndw_location(hass, "NL-NUO-near")
    entry, dynamic = await _add_dynamic(hass)
    coordinator = entry.runtime_data.dynamic[dynamic.subentry_id]
    sensor = _entity(hass, "sensor", f"{dynamic.subentry_id}_nearest")
    tracker = _entity(hass, "device_tracker", f"{dynamic.subentry_id}_nearest_tracker")

    _where(hass, HOME_LAT + 1.0, HOME_LON)  # next to "Verweg 9"
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "Verweg 9"

    _where(hass, HOME_LAT + 0.5, HOME_LON)  # halfway: nothing within 1 km
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "unknown"
    assert hass.states.get(tracker).state == "unknown"

    hass.states.async_set("person.me", "unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "unknown"


async def test_dynamic_event_fires_when_a_different_free_charger_becomes_best(hass, mock_ndw):
    _where(hass)
    await _add_ndw_location(hass, "NL-NUO-near")
    entry, dynamic = await _add_dynamic(hass)
    coordinator = entry.runtime_data.dynamic[dynamic.subentry_id]
    event = _entity(hass, "event", f"{dynamic.subentry_id}_nearest_event")
    assert hass.states.get(event).attributes.get("event_type") is None

    await _update_feed(hass, mock_ndw, 0)  # the nearest one is full now
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    attributes = hass.states.get(event).attributes
    assert attributes["event_type"] == "nearest_changed"
    assert attributes["address"] == "Teststraat 2"


async def test_removing_the_device_removes_the_dynamic_location(hass, mock_ndw):
    from homeassistant.helpers import device_registry as dr

    _where(hass)
    await _add_ndw_location(hass, "NL-NUO-near")
    entry, dynamic = await _add_dynamic(hass)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"dynamic_{dynamic.subentry_id}"), entry.entry_id
    )
    assert device is not None
    from custom_components.public_charge_watch import async_remove_config_entry_device

    assert await async_remove_config_entry_device(hass, entry, device)
    await hass.async_block_till_done()
    assert dynamic.subentry_id not in entry.subentries
