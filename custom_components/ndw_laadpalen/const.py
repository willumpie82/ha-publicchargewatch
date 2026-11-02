"""Constants for the NDW Laadpalen integration."""

DOMAIN = "ndw_laadpalen"

NDW_BASE_URL = "https://opendata.ndw.nu/"
FEED_URL = NDW_BASE_URL + "charging_point_locations.geojson.gz"
TARIFF_URL = NDW_BASE_URL + "charging_point_tariffs_ocpi.json.gz"

CONF_SOURCE = "source"
CONF_API_KEY = "api_key"
CONF_LOCATION = "location"
CONF_LOCATION_ID = "location_id"
CONF_RADIUS = "radius"
CONF_ZONE = "zone"
CONF_POSTCODE = "postcode"
CONF_HOUSE_NUMBER = "house_number"
CONF_SCAN_INTERVAL = "scan_interval"

SOURCE_NDW = "ndw"  # Netherlands only; live availability; ~5 MB per refresh
SOURCE_OCM = "ocm"  # worldwide; API key; no live occupancy; small requests
OCM_API_URL = "https://openchargemap.org/site/develop/api"

DEFAULT_RADIUS_M = 200
MAX_RADIUS_M = 500
# (default, minimum) update interval in seconds per source
SCAN_INTERVALS = {SOURCE_NDW: (300, 120), SOURCE_OCM: (1800, 300)}
MIN_FEED_AGE = 60  # seconds; NDW entries share one download within this window
TARIFF_MAX_AGE = 24 * 3600  # tariffs change rarely: at most one download per day
TARIFF_RETRY = 3600  # after a failed tariff download

# Lowercased OCPI-style statuses for entity states (OCM per-connection sensors)
EVSE_STATUSES = [
    "available",
    "blocked",
    "charging",
    "inoperative",
    "operational",  # OCM only: believed to work, occupancy unknown
    "outoforder",
    "planned",
    "removed",
    "reserved",
    "unknown",
]

SUBENTRY_LOCATION = "location"  # one subentry per place (city)
CONF_CITY = "city"
CONF_LOCATION_IDS = "location_ids"
CONF_NOTIFY = "notify_entities"  # {location_id: entity_id} that gates availability events
CONF_NOTIFY_ENTITY = "notify_entity"
