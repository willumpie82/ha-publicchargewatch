"""Static checks: brand images and entity icons line up with the code."""

import json
from pathlib import Path

from PIL import Image

from custom_components.ndw_laadpalen.const import EVSE_STATUSES

PKG = Path(__file__).parent.parent / "custom_components/ndw_laadpalen"


def test_brand_images():
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        image = Image.open(PKG / "brand" / name)
        assert image.size == (size, size)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0, "corners must be transparent"


def test_icons_cover_every_translated_entity():
    icons = json.loads((PKG / "icons.json").read_text())["entity"]
    names = json.loads((PKG / "translations/en.json").read_text())["entity"]
    for platform, entities in names.items():
        for key in entities:
            assert key in icons.get(platform, {}), f"no icon for {platform}.{key}"
            assert "default" in icons[platform][key]


def test_status_icons_cover_all_states():
    states = json.loads((PKG / "icons.json").read_text())["entity"]["sensor"]["evse_status"]["state"]
    assert set(states) == set(EVSE_STATUSES)
