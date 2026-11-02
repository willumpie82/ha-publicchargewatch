[Setup](setup.md) · [Entities](entities.md) · [Dynamic charging location](dynamic.md) · [Examples](examples.md) · [Development](development.md)

# Development
```
# integration tests (Home Assistant, mocked network)
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python pytest-homeassistant-custom-component ijson==3.3.0
.venv/bin/python -m pytest

# checks against the live services (no Home Assistant needed)
pip install ijson==3.3.0 aiohttp
python scripts/check_ndw.py              # NDW locations + tariffs
python scripts/check_geocode.py          # PDOK postcode lookup
python scripts/check_tariff.py           # OCM price text parser, offline
OCM_API_KEY=... python scripts/check_ocm.py
```
