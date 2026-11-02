"""Constants for the PublicChargeWatch integration."""

DOMAIN = "public_charge_watch"

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

# Dynamic charging location: the charger nearest to a tracked entity (NDW only)
SUBENTRY_DYNAMIC = "dynamic"
CONF_TRACKED = "tracked_entity"
CONF_CONNECTORS = "connector_types"  # NDW connector_type values; empty = any
CONF_MIN_POWER = "min_power_kw"
CONF_ONLY_AVAILABLE = "only_available"
DEFAULT_DYNAMIC_RADIUS_M = 1500
MAX_DYNAMIC_RADIUS_M = 10_000
DYNAMIC_RESULTS = 3  # chargers listed in the sensor attributes
DYNAMIC_MOVE_M = 150  # recompute when the tracked entity moved this far
TARIFF_LAZY_AGE = 3600  # new locations met while moving may trigger a tariff download this often

# NDW DOT-NL REST API: only the area asked for (a bounding box), no bulk download
DOTNL_URL = (
    "https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/charge-point-data/v1/features"
)
DOTNL_MAX_BBOX_DEG2 = 1.0  # the API refuses larger areas
DOTNL_REQUEST_GAP = 0.11  # seconds between requests (the API is rate limited)
DOTNL_LOCATION_RADIUS_M = 150  # area asked for around a known location
CONF_COORDS = "coordinates"  # {location_id: [latitude, longitude]} stored in a place
