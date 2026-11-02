"""Drive the subentry flow through the REST API like the frontend does."""

from homeassistant.setup import async_setup_component

from custom_components.ndw_laadpalen.const import DOMAIN

from .test_flows import _add_ndw_location


async def test_subentry_over_http(hass, hass_client, mock_ndw):
    assert await async_setup_component(hass, "config", {})
    await _add_ndw_location(hass, "NL-NUO-near")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    client = await hass_client()
    base = "/api/config/config_entries/subentries/flow"

    async def post(url, body=None):
        resp = await client.post(url, json=body)
        data = await resp.json()
        return data

    r = await post(base, {"handler": [entry.entry_id, "location"], "show_advanced_options": False})
    url = f"{base}/{r['flow_id']}"
    # the frontend cannot start a subentry flow with a progress screen
    assert r["type"] == "menu"
    for body in ({"next_step_id": "zone"}, {"zone": "zone.home", "radius": 200}):
        while r["type"] in ("progress", "progress_done"):
            await hass.async_block_till_done()
            r = await post(url, {})
        r = await post(url, body)
    while r["type"] in ("progress", "progress_done"):
        await hass.async_block_till_done()
        r = await post(url, {})
    r = await post(url, {"location_id": "NL-NUO-two"})
    assert len(entry.subentries) == 2
