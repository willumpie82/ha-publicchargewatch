"""End-to-end tests: onboarding flows and the entities they create."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.ndw_laadpalen.const import DOMAIN, TARIFF_URL
from custom_components.ndw_laadpalen.geocode import PDOK_URL
from custom_components.ndw_laadpalen.ocm import OCM_URL

from .conftest import HOME_LAT, HOME_LON, NDW_FEED, _gz
from custom_components.ndw_laadpalen.const import FEED_URL


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


async def _ndw_to_search_method(hass):
    hass.config.country = "NL"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"
    return await _configure(hass, result, {"source": "ndw"})


async def _add_ndw_location(hass, location_id):
    result = await _ndw_to_search_method(hass)
    if result["type"] is FlowResultType.MENU and result["step_id"] == "ndw_confirm":
        result = await _configure(hass, result, {"next_step_id": "fetch"})
    result = await _progress(hass, result)
    assert result["step_id"] == "location_method"
    result = await _configure(hass, result, {"next_step_id": "zone"})
    result = await _configure(hass, result, {"zone": "zone.home", "radius": 200})
    result = await _progress(hass, result)
    assert result["step_id"] == "select"
    result = await _configure(hass, result, {"location_id": location_id})
    assert result["step_id"] == "interval_ndw"
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


async def test_second_location_is_a_subentry_of_the_same_entry(hass, mock_ndw):
    first = await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert first["title"] == "NDW" and entry.unique_id == "ndw"
    assert len(entry.subentries) == 1

    # a second NDW entry is refused: locations are added to the existing one
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    sources = result["data_schema"].schema["source"].config["options"]
    assert sources == ["ocm"]

    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "location"), context={"source": SOURCE_USER}
    )
    flow = await _progress_sub(hass, flow)
    assert flow["step_id"] == "location_method"  # no second download warning
    flow = await _configure_sub(hass, flow, {"next_step_id": "zone"})
    flow = await _configure_sub(hass, flow, {"zone": "zone.home", "radius": 200})
    flow = await _progress_sub(hass, flow)
    assert flow["step_id"] == "select"
    selector = next(v for k, v in flow["data_schema"].schema.items() if k == "location_id")
    values = [o["value"] for o in selector.config["options"]]
    assert "NL-NUO-near" not in values  # already added
    flow = await _configure_sub(hass, flow, {"location_id": "NL-NUO-two"})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert len(entry.subentries) == 2
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-two_available")).state == "2"
    assert hass.states.get(_entity(hass, "sensor", "NL-NUO-near_available")).state == "1"
    checked = hass.states.get(_entity(hass, "sensor", "NL-NUO-near_checked"))
    assert checked.attributes["last_update_ok"] is True


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
    selector = next(v for k, v in result["data_schema"].schema.items() if k == "location_id")
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
    result = await _configure(hass, result, {"location_id": "ocm-473176"})
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
    from custom_components.ndw_laadpalen.const import FEED_URL

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

    from custom_components.ndw_laadpalen.api import NdwFeed
    from custom_components.ndw_laadpalen.const import FEED_URL

    feed = NdwFeed(hass, async_get_clientsession(hass))
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
