"""What every charging-data source implements (no Home Assistant imports).

A provider answers two questions: which locations are around a point (used by the setup
flows and the dynamic location) and what is the current state of these known locations
(used by the coordinator). Locations are dicts in one shared shape: `id`, `address`, `city`,
`operator`, `latitude`, `longitude`, `last_updated`, `points`, and for live sources `groups`
(connector type, power, total, available) plus price fields.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class ProviderError(Exception):
    """Base class of the errors a provider raises on its own."""


class ProviderAuthError(ProviderError):
    """The source rejected the credentials."""


class ProviderRateLimitError(ProviderError):
    """The source asked us to slow down."""

    def __init__(self, message: str = "", retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Provider(ABC):
    """One source of charging locations."""

    provider_id: ClassVar[str]  # also the unique id of its config entry
    name: ClassVar[str]
    live_occupancy: ClassVar[bool]  # knows free / in use, not just "working"
    needs_api_key: ClassVar[bool] = False
    bulk_download: ClassVar[bool] = False  # works on a big shared file (a flow shows its download)
    city_from_geocoder: ClassVar[bool] = False  # locations carry no town: ask a geocoder
    scan_interval: ClassVar[tuple[int, int]]  # (default, minimum) in seconds

    @abstractmethod
    async def async_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,
    ) -> list[dict[str, Any]]:
        """Locations around a point, nearest first, each with a 'distance' field."""

    async def async_nearby_live(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        max_age: float | None = None,
    ) -> list[dict[str, Any]]:
        """Like async_nearby, with prices attached where the source has them."""
        return await self.async_nearby(latitude, longitude, radius_m, max_age)

    @abstractmethod
    async def async_get_locations(
        self, ids: set[str], force: bool = False, max_age: float | None = None
    ) -> dict[str, dict[str, Any]]:
        """Current state of known locations; force skips any shared-data reuse."""

    async def async_validate(self) -> None:
        """Raise ProviderAuthError when the credentials are rejected."""

    async def async_prepare(self, max_age: float | None = None) -> None:
        """Get shared data ready (bulk sources download their file here)."""

    def is_fresh(self, max_age: float) -> bool:  # noqa: ARG002
        """Whether a search can run without first fetching shared data."""
        return True
