"""End-to-end tests: onboarding flows and the entities they create."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.public_charge_watch.const import DOMAIN, TARIFF_URL
from custom_components.public_charge_watch.geocoding import PDOK_URL
from custom_components.public_charge_watch.providers.ocm import OCM_URL

from .conftest import HOME_LAT, HOME_LON, NDW_FEED, _gz
from custom_components.public_charge_watch.const import FEED_URL


async def _progress(hass, result):
    """Wait for a progress step to finish and move on (mocked I/O may already be done)."""
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        return result
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def _configure(hass, result, user_input):
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


def _entity(hass, domain, unique_id):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def _ndw_to_search_method(hass, source="ndw"):
    hass.config.country = "NL"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"
    return await _configure(hass, result, {"source": source})


async def _add_ndw_location(hass, location_id, source="ndw"):
    result = await _ndw_to_search_method(hass, source)
    if result["type"] is FlowResultType.MENU and result["step_id"] == "ndw_confirm":
        result = await _configure(hass, result, {"next_step_id": "fetch"})
    result = await _progress(hass, result)
    assert result["step_id"] == "location_method"
    result = await _configure(hass, result, {"next_step_id": "zone"})
    result = await _configure(hass, result, {"zone": "zone.home", "radius": 200})
    result = await _progress(hass, result)
    assert result["step_id"] == "select"
    ids = [location_id] if isinstance(location_id, str) else location_id
    result = await _configure(hass, result, {"location_ids": ids})
    assert result["step_id"] in ("interval_ndw", "interval_api")
    result = await _configure(hass, result, {"scan_interval": 300})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result


async def test_ndw_confirm_is_explained_and_can_go_back(hass, mock_ndw):
    result = await _ndw_to_search_method(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "ndw_confirm"
    assert set(result["menu_options"]) == {"fetch", "user"}
    assert not mock_ndw.mock_calls, "nothing may be downloaded before the user confirms"
    result = await _configure(hass, result, {"next_step_id": "user"})
    assert result["step_id"] == "user"


async def test_ndw_flow_creates_entities_from_the_data(hass, mock_ndw):
    result = await _add_ndw_location(hass, "NL-NUO-near")
    assert result["title"] == "NDW"
    assert result["options"] == {"scan_interval": 300}

    available = hass.states.get(_entity(hass, "sensor", "NL-NUO-near_available"))
    assert available.state == "1"
    assert available.attributes["total_points"] == 2

    price = hass.states.get(_entity(hass, "sensor", "NL-NUO-near_price"))
    assert float(price.state) == 0.3256  # 0.2691 * 1.21
    assert price.attributes["price_basis"] == "tariff"
    assert price.attributes["vat_percent"] == 21.0

    assert hass.states.get(_entity(hass, "binary_sensor", "NL-NUO-near_any_available")).state == "on"
    updated = hass.states.get(_entity(hass, "sensor", "NL-NUO-near_updated"))
    assert updated.state.startswith("2026-11-02T06:45:39")
    # one connector group: a group sensor would only repeat the location total
    assert _entity(hass, "sensor", "NL-NUO-near_IEC_62196_T2_11040_AC3") is None


async def test_group_sensors_only_when_there_are_several_groups(hass, mock_ndw):
    await _add_ndw_location(hass, "NL-NUO-two")
    ccs = hass.states.get(_entity(hass, "sensor", "NL-NUO-two_IEC_62196_T2_COMBO_50000_AC3"))
    assert ccs.state == "0"
    assert ccs.attributes["price_per_kwh"] == 0.605  # 0.5 * 1.21
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-two_available")).state == "2"


async def _add_via_subentry(hass, entry, location_id):
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "location"), context={"source": SOURCE_USER}
    )
    assert flow["type"] is FlowResultType.MENU  # nothing slow before the first screen
    flow = await _configure_sub(hass, flow, {"next_step_id": "zone"})
    flow = await _configure_sub(hass, flow, {"zone": "zone.home", "radius": 200})
    flow = await _progress_sub(hass, flow)
    assert flow["step_id"] == "select"
    selector = next(v for k, v in flow["data_schema"].schema.items() if k == "location_ids")
    values = [o["value"] for o in selector.config["options"]]
    flow = await _configure_sub(hass, flow, {"location_ids": [location_id]})
    await hass.async_block_till_done()
    return flow, values


async def test_location_in_the_same_place_joins_that_place(hass, mock_ndw):
    first = await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert first["title"] == "NDW" and entry.unique_id == "ndw"
    (place,) = entry.subentries.values()
    assert place.title == "Teststad" and place.data["location_ids"] == ["NL-NUO-near"]

    # a second NDW entry is refused: locations are added to the existing one
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["data_schema"].schema["source"].config["options"] == ["ndw_api", "ocm"]

    flow, values = await _add_via_subentry(hass, entry, "NL-NUO-two")
    assert "NL-NUO-near" not in values  # already added
    assert flow["type"] is FlowResultType.ABORT and flow["reason"] == "location_added"
    assert len(entry.subentries) == 1
    (place,) = entry.subentries.values()
    assert place.data["location_ids"] == ["NL-NUO-near", "NL-NUO-two"]
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-two_available")).state == "2"
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-near_available")).state == "1"
    checked = hass.states.get(_entity(hass, "sensor", "NL-NUO-near_checked"))
    assert checked.attributes["last_update_ok"] is True


async def test_location_in_another_place_creates_a_place(hass, mock_ndw):
    from .conftest import mock_city

    await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    mock_city(mock_ndw, "Anderstad")
    flow, _ = await _add_via_subentry(hass, entry, "NL-NUO-two")
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    assert sorted(p.title for p in entry.subentries.values()) == ["Anderstad", "Teststad"]


async def test_removing_a_device_removes_the_location_from_its_place(hass, mock_ndw):
    from homeassistant.helpers import device_registry as dr

    await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _add_via_subentry(hass, entry, "NL-NUO-two")
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "NL-NUO-two"), entry.entry_id
    )
    from custom_components.public_charge_watch import async_remove_config_entry_device

    assert await async_remove_config_entry_device(hass, entry, device)
    (place,) = entry.subentries.values()
    assert place.data["location_ids"] == ["NL-NUO-near"]


async def _configure_sub(hass, result, user_input):
    return await hass.config_entries.subentries.async_configure(result["flow_id"], user_input)


async def _progress_sub(hass, result):
    while result["type"] in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE):
        await hass.async_block_till_done()
        result = await hass.config_entries.subentries.async_configure(result["flow_id"])
    return result


async def test_tariff_failure_does_not_break_availability(hass, aioclient_mock):
    aioclient_mock.get(FEED_URL, content=_gz(NDW_FEED), headers={"ETag": '"v1"'})
    aioclient_mock.get(TARIFF_URL, status=500)
    from .conftest import mock_city

    mock_city(aioclient_mock)
    await _add_ndw_location(hass, "NL-NUO-near")
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-near_available")).state == "1"
    assert _entity(hass, "sensor", "NL-NUO-near_price") is None  # no price known -> no entity


async def test_postcode_search(hass, mock_ndw):
    mock_ndw.get(
        PDOK_URL,
        json={"response": {"docs": [{"postcode": "5223HS", "centroide_ll": f"POINT({HOME_LON} {HOME_LAT})"}]}},
    )
    result = await _ndw_to_search_method(hass)
    result = await _configure(hass, result, {"next_step_id": "fetch"})
    result = await _progress(hass, result)
    assert "postcode" in result["menu_options"]  # country is NL
    result = await _configure(hass, result, {"next_step_id": "postcode"})

    bad = await _configure(hass, result, {"postcode": "123", "radius": 200})
    assert bad["errors"] == {"postcode": "postcode_invalid"}

    result = await _configure(hass, bad, {"postcode": "5223 hs", "house_number": "23", "radius": 200})
    result = await _progress(hass, result)
    assert result["step_id"] == "select"
    selector = next(v for k, v in result["data_schema"].schema.items() if k == "location_ids")
    labels = [o["label"] for o in selector.config["options"]]
    assert labels[0].startswith("Teststraat 1 · Vattenfall InCharge · 1/2 ✓")
    assert "€0.3256/kWh" in labels[0]


async def test_no_results_offers_a_retry(hass, mock_ndw):
    result = await _ndw_to_search_method(hass)
    result = await _configure(hass, result, {"next_step_id": "fetch"})
    result = await _progress(hass, result)
    result = await _configure(hass, result, {"next_step_id": "map"})
    result = await _configure(
        hass, result, {"location": {"latitude": 10.0, "longitude": 10.0, "radius": 200}}
    )
    result = await _progress(hass, result)
    assert result["step_id"] == "problem"
    assert result["errors"] == {"base": "no_locations"}
    result = await _configure(hass, result, {})
    assert result["step_id"] == "location_method"


async def _add_ocm_location(hass, key="secret"):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _configure(hass, result, {"source": "ocm"})
    assert result["step_id"] == "api_key"
    result = await _configure(hass, result, {"api_key": key})
    return result


async def test_ocm_rejects_an_invalid_key(hass, aioclient_mock):
    aioclient_mock.get(OCM_URL, status=403)
    result = await _add_ocm_location(hass)
    assert result["step_id"] == "api_key"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_ocm_flow_has_no_ndw_download_and_other_entities(hass, mock_ocm):
    result = await _add_ocm_location(hass)
    assert result["step_id"] == "location_method"
    result = await _configure(hass, result, {"next_step_id": "zone"})
    result = await _configure(hass, result, {"zone": "zone.home", "radius": 200})
    result = await _progress(hass, result)
    assert result["step_id"] == "select"
    result = await _configure(hass, result, {"location_ids": ["ocm-473176"]})
    assert result["step_id"] == "interval_ocm"
    result = await _configure(hass, result, {"scan_interval": 1800})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["api_key"] == "secret"
    await hass.async_block_till_done()

    operational = hass.states.get(_entity(hass, "sensor", "ocm-473176_operational"))
    assert operational.state == "2"  # Quantity 2
    price = hass.states.get(_entity(hass, "sensor", "ocm-473176_price"))
    assert float(price.state) == 0.34
    assert price.attributes["price_basis"] == "per_kwh"
    # OCM cannot tell free from busy: no availability entities
    assert _entity(hass, "sensor", "ocm-473176_available") is None
    assert _entity(hass, "binary_sensor", "ocm-473176_any_available") is None
    status = hass.states.get(_entity(hass, "sensor", "ocm-473176-794706_status"))
    assert status.state == "operational"


async def test_refresh_button_forces_a_new_check(hass, mock_ndw):
    from custom_components.public_charge_watch.const import FEED_URL

    await _add_ndw_location(hass, "NL-NUO-near")
    button = _entity(hass, "button", "NL-NUO-near_refresh")
    assert button is not None
    assert er.async_get(hass).async_get(button).entity_category == "diagnostic"

    feed_calls = lambda: sum(1 for m in mock_ndw.mock_calls if str(m[1]) == FEED_URL)  # noqa: E731
    before = feed_calls()
    # within the shared 60 s window a normal refresh would reuse the cache
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert feed_calls() == before + 1


async def test_entries_share_one_download(hass, mock_ndw):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.public_charge_watch.providers.ndw_download import NdwFeed
    from custom_components.public_charge_watch.const import FEED_URL

    from custom_components.public_charge_watch.providers.ndw_shared import NdwTariffs

    session = async_get_clientsession(hass)
    feed = NdwFeed(hass, session, NdwTariffs(hass, session))
    calls = lambda: sum(1 for m in mock_ndw.mock_calls if str(m[1]) == FEED_URL)  # noqa: E731

    first = await feed.async_get_locations({"NL-NUO-near"}, max_age=240)
    assert "NL-NUO-near" in first and calls() == 1
    # a second location added later is found in the feed we already hold
    second = await feed.async_get_locations({"NL-NUO-two"}, max_age=240)
    assert "NL-NUO-two" in second and calls() == 1
    # and a poll of the first within the window is served from memory
    await feed.async_get_locations({"NL-NUO-near"}, max_age=240)
    assert calls() == 1
    # a window of 0 (data too old) and a forced refresh each check again
    await feed.async_get_locations({"NL-NUO-near"}, max_age=0)
    assert calls() == 2
    await feed.async_get_locations({"NL-NUO-near"}, max_age=240, force=True)
    assert calls() == 3
    # the cache covers every wanted location after a re-download
    both = await feed.async_get_locations({"NL-NUO-near", "NL-NUO-two"}, max_age=240)
    assert set(both) == {"NL-NUO-near", "NL-NUO-two"} and calls() == 3


async def test_stale_feed_is_downloaded_in_its_own_progress_step(hass, mock_ndw):
    await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.data[DOMAIN]["feed"]._fetched = -1e9  # far too old for a search
    before = sum(1 for m in mock_ndw.mock_calls if str(m[1]) == FEED_URL)

    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "location"), context={"source": SOURCE_USER}
    )
    assert flow["type"] is FlowResultType.MENU  # nothing slow before the first screen
    flow = await _configure_sub(hass, flow, {"next_step_id": "zone"})
    flow = await _configure_sub(hass, flow, {"zone": "zone.home", "radius": 200})
    # with mocked I/O the download may already be finished; either way it is its own step
    flow = await _progress_sub(hass, flow)
    assert flow["step_id"] == "select"
    assert sum(1 for m in mock_ndw.mock_calls if str(m[1]) == FEED_URL) == before + 1


async def _update_feed(hass, mock_ndw, available):
    """Serve a feed where NL-NUO-near has this many free points and refresh."""
    from .conftest import NEAR_FREE

    NEAR_FREE["n"] = available
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    entry.runtime_data._force = True  # the button's refresh is debounced; refresh directly
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_events_fire_when_a_location_gets_free_or_full(hass, mock_ndw):
    await _add_ndw_location(hass, "NL-NUO-near")  # 1 of 2 free
    event = _entity(hass, "event", "NL-NUO-near_availability")
    assert event is not None
    async def last_type():
        return hass.states.get(event).attributes.get("event_type")

    await _update_feed(hass, mock_ndw, 0)
    assert await last_type() == "became_occupied"
    await _update_feed(hass, mock_ndw, 2)
    assert await last_type() == "became_available"
    assert hass.states.get(event).attributes["free"] == 2


async def test_notify_entity_gates_the_events(hass, mock_ndw):
    await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    (place,) = entry.subentries.values()

    # reconfigure: choose a notify entity for the location
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "location"),
        context={"source": "reconfigure", "subentry_id": place.subentry_id},
    )
    assert flow["step_id"] == "notify"  # one location in the place: no choice needed
    flow = await _configure_sub(hass, flow, {"notify_entity": "input_boolean.need_charge"})
    assert flow["type"] is FlowResultType.ABORT and flow["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    (place,) = entry.subentries.values()
    assert place.data["notify_entities"] == {"NL-NUO-near": "input_boolean.need_charge"}

    event = _entity(hass, "event", "NL-NUO-near_availability")
    hass.states.async_set("input_boolean.need_charge", "off")
    await _update_feed(hass, mock_ndw, 0)
    assert hass.states.get(event).attributes.get("event_type") is None  # gate closed

    hass.states.async_set("input_boolean.need_charge", "on")
    await _update_feed(hass, mock_ndw, 1)
    assert hass.states.get(event).attributes["event_type"] == "became_available"


async def test_several_locations_can_be_picked_at_once(hass, mock_ndw):
    await _add_ndw_location(hass, ["NL-NUO-near", "NL-NUO-two"])
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    (place,) = entry.subentries.values()  # same town: one place holding both
    assert place.data["location_ids"] == ["NL-NUO-near", "NL-NUO-two"]
    assert _entity(hass, "sensor", "NL-NUO-two_available") is not None
